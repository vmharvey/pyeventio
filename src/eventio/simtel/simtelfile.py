'''
Implementation of an EventIOFile that
loops through SimTel Array events.
'''
import re
from copy import copy
from collections import defaultdict
import warnings
import logging

from ..base import EventIOFile
from ..exceptions import check_type
from .. import iact
from ..histograms import Histograms
from .objects import (
    ADCSamples,
    ADCSums,
    ArrayEvent,
    AuxiliaryAnalogTraces,
    AuxiliaryDigitalTraces,
    CalibrationEvent,
    CameraMonitoring,
    CameraOrganization,
    CameraSettings,
    CameraSoftwareSettings,
    DisabledPixels,
    DriveSettings,
    History,
    HistoryMeta,
    ImageParameters,
    LaserCalibration,
    MCEvent,
    MCPhotoelectronSum,
    MCRunHeader,
    MCShower,
    PixelList,
    PixelSettings,
    PixelTiming,
    PixelTriggerTimes,
    PixelMonitoring,
    PointingCorrection,
    RunHeader,
    TelescopeEvent,
    TelescopeEventHeader,
    TrackingPosition,
    TriggerInformation,
    CalibrationPhotoelectrons,
)


telescope_descriptions_types = (
    CameraSettings,
    CameraOrganization,
    PixelSettings,
    DisabledPixels,
    CameraSoftwareSettings,
    DriveSettings,
    PointingCorrection,
)


class UnknownObjectWarning(UserWarning):
    pass


log = logging.getLogger(__name__)


camel_re1 = re.compile('(.)([A-Z][a-z]+)')
camel_re2 = re.compile('([a-z0-9])([A-Z])')


def camel_to_snake(name):
    s1 = camel_re1.sub(r'\1_\2', name)
    return camel_re2.sub(r'\1_\2', s1).lower()


class NoTrackingPositions(Exception):
    pass


class SimTelFile(EventIOFile):
    def __init__(self, path, allowed_telescopes=None, skip_calibration=False, zcat=True):
        super().__init__(path, zcat=zcat)

        self.path = path
        self.allowed_telescopes = None
        if allowed_telescopes:
            self.allowed_telescopes = set(allowed_telescopes)

        self.histograms = None

        self.history = []
        self.mc_run_headers = []
        self.corsika_inputcards = []
        self.atmospheric_profiles = []
        self.header = None
        self.n_telescopes = None
        self.telescope_meta = {}
        self.global_meta = {}
        self.telescope_descriptions = defaultdict(dict)
        self.pixel_monitorings = defaultdict(dict)
        self.camera_monitorings = defaultdict(dict)
        self.laser_calibrations = defaultdict(dict)
        self.current_mc_shower = None
        self.current_mc_shower_id = None
        self.current_mc_event = None
        self.current_mc_event_id = None
        self.current_telescope_data_event_id = None
        self.current_photoelectron_sum = None
        self.current_photoelectron_sum_event_id = None
        self.current_photoelectrons = {}
        self.current_photons = {}
        self.current_emitter = {}
        self.current_array_event = None
        self.current_calibration_event = None
        self.current_calibration_event_id = None
        self.current_calibration_pe = {}
        self.skip_calibration = skip_calibration

        # read the header:
        # assumption: the header is done when
        # any of the objects in check is not None anymore
        # and we found the telescope_descriptions of all telescopes
        check = []
        found_all_telescopes = False
        while not (any(o for o in check) and found_all_telescopes):
            self.next_low_level()

            check = [
                self.current_mc_shower,
                self.current_array_event,
                self.current_calibration_event,
                self.laser_calibrations,
                self.camera_monitorings,
            ]

            # check if we found all the descriptions of all telescopes
            if self.n_telescopes is not None:
                found = sum(
                    len(t) == len(telescope_descriptions_types)
                    for t in self.telescope_descriptions.values()
                )
                found_all_telescopes = found == self.n_telescopes

    def __iter__(self):
        return self.iter_array_events()

    def next_low_level(self):
        o = next(self)

        # order of if statements is roughly sorted
        # by the number of occurences in a simtel file
        # this should minimize the number of if statements evaluated

        if isinstance(o, MCEvent):
            self.current_mc_event = o.parse()
            self.current_mc_event_id = o.header.id

        elif isinstance(o, MCShower):
            self.current_mc_shower = o.parse()
            self.current_mc_shower_id = o.header.id

        elif isinstance(o, ArrayEvent):
            self.current_array_event = parse_array_event(
                o,
                self.allowed_telescopes
            )

        elif isinstance(o, iact.TelescopeData):
            event_id, photons, emitter, photoelectrons = parse_telescope_data(o)
            self.current_telescope_data_event_id = event_id
            self.current_photons = photons
            self.current_emitter = emitter
            self.current_photoelectrons = photoelectrons

        elif isinstance(o, MCPhotoelectronSum):
            self.current_photoelectron_sum_event_id = o.header.id
            self.current_photoelectron_sum = o.parse()

        elif isinstance(o, CameraMonitoring):
            self.camera_monitorings[o.telescope_id].update(o.parse())

        elif isinstance(o, LaserCalibration):
            self.laser_calibrations[o.telescope_id].update(o.parse())

        elif isinstance(o, PixelMonitoring):
            self.pixel_monitorings[o.telescope_id].update(o.parse())

        elif isinstance(o, telescope_descriptions_types):
            key = camel_to_snake(o.__class__.__name__)
            self.telescope_descriptions[o.telescope_id][key] = o.parse()

        elif isinstance(o, RunHeader):
            self.header = o.parse()
            self.n_telescopes = self.header['n_telescopes']

        elif isinstance(o, MCRunHeader):
            self.mc_run_headers.append(o.parse())

        elif isinstance(o, iact.InputCard):
            self.corsika_inputcards.append(o.parse())

        elif isinstance(o, CalibrationEvent):
            if not self.skip_calibration:
                array_event = next(o)
                self.current_calibration_event = parse_array_event(
                    array_event,
                    self.allowed_telescopes,
                )
                # assign negative event_ids to calibration events to avoid
                # duplicated event_ids
                self.current_calibration_event_id = -array_event.header.id
                self.current_calibration_event['calibration_type'] = o.type
        elif isinstance(o, CalibrationPhotoelectrons):
            telescope_data = next(o)
            if not isinstance(telescope_data, iact.TelescopeData):
                warnings.warn(
                    f"Unexpected sub-object: {telescope_data} in {o}, ignoring",
                    UnknownObjectWarning
                )
                return

            self.current_calibration_pe = {}
            for photoelectrons in telescope_data:
                if not isinstance(photoelectrons, iact.PhotoElectrons):
                    warnings.warn(
                        f"Unexpected sub-object: {photoelectrons} in {telescope_data}, ignoring",
                        UnknownObjectWarning
                    )

                tel_id = photoelectrons.telescope_id
                self.current_calibration_pe[tel_id] = photoelectrons.parse()


        elif isinstance(o, History):
            for sub in o:
                self.history.append(sub.parse())

        elif isinstance(o, HistoryMeta):
            if o.header.id == -1:
                self.global_meta = o.parse()
            else:
                self.telescope_meta[o.header.id] = o.parse()

        elif isinstance(o, Histograms):
            self.histograms = o.parse()
        elif isinstance(o, iact.AtmosphericProfile):
            self.atmospheric_profiles.append(o.parse())
        else:
            warnings.warn(
                'object type encountered, which is no handled'
                ' at the moment: {}'.format(o),
                UnknownObjectWarning,
            )

    def iter_mc_events(self):
        while True:
            try:
                next_event = self.try_build_mc_event()
            except StopIteration:
                break
            if next_event is not None:
                yield next_event

    def try_build_mc_event(self):
        if self.current_mc_event:

            event_data = {
                'event_id': self.current_mc_event_id,
                'mc_shower': self.current_mc_shower,
                'mc_event': self.current_mc_event,
            }
            # if next object is TelescopeData, it belongs to this event
            if isinstance(self.peek(), iact.TelescopeData):
                self.next_low_level()
                event_data['photons'] = self.current_photons
                event_data['emitter'] = self.current_emitter
                event_data['photoelectrons'] = self.current_photoelectrons

            self.current_mc_event = None
            return event_data
        self.next_low_level()

    def iter_array_events(self):
        while True:

            next_event = self.try_build_event()
            if next_event is not None:
                yield next_event

            try:
                self.next_low_level()
            except StopIteration:
                break

    def try_build_event(self):
        '''check if all necessary info for an event was found,
        then make an event and invalidate old data
        '''
        if self.current_array_event:
            if (
                self.allowed_telescopes
                and not self.current_array_event['telescope_events']
            ):
                self.current_array_event = None
                return None

            event_id = self.current_array_event['event_id']

            event_data = {
                'type': 'data',
                'event_id': event_id,
                'mc_shower': None,
                'mc_event': None,
                'telescope_events': self.current_array_event['telescope_events'],
                'tracking_positions': self.current_array_event['tracking_positions'],
                'trigger_information': self.current_array_event['trigger_information'],
                'photons': {},
                'emitter': {},
                'photoelectrons': {},
                'photoelectron_sums': None,
            }

            if self.current_mc_event_id == event_id:
                event_data['mc_shower'] = self.current_mc_shower
                event_data['mc_event'] =  self.current_mc_event

            if self.current_telescope_data_event_id == event_id:
                event_data['photons'] = self.current_photons
                event_data['emitter'] = self.current_emitter
                event_data['photoelectrons'] = self.current_photoelectrons

            if self.current_photoelectron_sum_event_id == event_id:
                event_data['photoelectron_sums'] = self.current_photoelectron_sum

            event_data['camera_monitorings'] = {
                telescope_id: copy(self.camera_monitorings[telescope_id])
                for telescope_id in self.current_array_event['telescope_events'].keys()
            }
            event_data['laser_calibrations'] = {
                telescope_id: copy(self.laser_calibrations[telescope_id])
                for telescope_id in self.current_array_event['telescope_events'].keys()
            }

            event_data['pixel_monitorings'] = {
                telescope_id: copy(self.pixel_monitorings[telescope_id])
                for telescope_id in self.current_array_event['telescope_events'].keys()
            }

            self.current_array_event = None

            return event_data

        elif self.current_calibration_event:
            event = self.current_calibration_event
            if (
                self.allowed_telescopes
                and not self.current_array_event['telescope_events']
            ):
                self.current_calibration_event = None
                return None

            event_data = {
                'type': 'calibration',
                'event_id': self.current_calibration_event_id,
                'telescope_events': event['telescope_events'],
                'tracking_positions': event['tracking_positions'],
                'trigger_information': event['trigger_information'],
                'calibration_type': event['calibration_type'],
                'photoelectrons': self.current_calibration_pe,
            }

            event_data['camera_monitorings'] = {
                telescope_id: copy(self.camera_monitorings[telescope_id])
                for telescope_id in event['telescope_events'].keys()
            }
            event_data['laser_calibrations'] = {
                telescope_id: copy(self.laser_calibrations[telescope_id])
                for telescope_id in event['telescope_events'].keys()
            }

            self.current_calibration_event = None

            return event_data


def parse_array_event(array_event, allowed_telescopes=None):
    '''structure of event:
        TriggerInformation[2009]  <-- this knows how many TelescopeEvents

        TelescopeEvent[2202]
        ...
        TelescopeEvent[2208]

        TrackingPosition[2101]
        ...
        TrackingPosition[2164]

        StereoReconstruction[2015]


        In words:
            1 cent event
            n tel events
            m track events (n does not need to be == m)
            1 shower
    '''
    check_type(array_event, ArrayEvent)

    telescope_events = {}
    tracking_positions = {}
    # for older files, the array_event.header.id does not match the mc event id
    # so we overwrite it later with the event id in the trigger information
    event_id = array_event.header.id

    for i, o in enumerate(array_event):
        # require first element to be a TriggerInformation
        if i == 0:
            check_type(o, TriggerInformation)
            event_id = o.header.id
            trigger_information = o.parse()
            telescopes = set(trigger_information['telescopes_with_data'])

            if allowed_telescopes and len(telescopes & allowed_telescopes) == 0:
                break

        elif isinstance(o, TelescopeEvent):
            if allowed_telescopes is None or o.telescope_id in allowed_telescopes:
                telescope_events[o.telescope_id] = parse_telescope_event(o)

        elif isinstance(o, TrackingPosition):
            if allowed_telescopes is None or o.telescope_id in allowed_telescopes:
                tracking_positions[o.telescope_id] = o.parse()

    missing_tracking = set(telescope_events.keys()) - set(tracking_positions.keys())
    if missing_tracking:
        raise NoTrackingPositions(
            'Missing tracking positions for telescopes {}'.format(
                missing_tracking
            )
        )

    return {
        'event_id': event_id,
        'trigger_information': trigger_information,
        'telescope_events': telescope_events,
        'tracking_positions': tracking_positions,
    }


def parse_telescope_data(telescope_data):
    ''' Parse the TelescopeData block with Cherenkov Photon information'''
    check_type(telescope_data, iact.TelescopeData)

    photons = {}
    emitter = {}
    photo_electrons = {}
    for o in telescope_data:
        if isinstance(o, iact.PhotoElectrons):
            photo_electrons[o.telescope_id] = o.parse()
        elif isinstance(o, iact.Photons):
            p, e = o.parse()
            photons[o.telescope_id] = p
            if e is not None:
                emitter[o.telescope_id] = e

    return telescope_data.header.id, photons, emitter, photo_electrons


def parse_telescope_event(telescope_event):
    '''Parse a telescope event'''
    check_type(telescope_event, TelescopeEvent)

    event = {'pixel_lists': {}}
    for i, o in enumerate(telescope_event):

        if i == 0:
            check_type(o, TelescopeEventHeader)
            event['header'] = o.parse()

        elif isinstance(o, ADCSamples):
            event['adc_samples'] = o.parse()

        elif isinstance(o, ADCSums):
            event['adc_sums'] = o.parse()

        elif isinstance(o, PixelTiming):
            event['pixel_timing'] = o.parse()

        elif isinstance(o, ImageParameters):
            event['image_parameters'] = o.parse()

        elif isinstance(o, PixelList):
            event['pixel_lists'][o.code] = o.parse()

        elif isinstance(o, PixelTriggerTimes):
            event['pixel_trigger_times'] = o.parse()
        elif isinstance(o, (AuxiliaryAnalogTraces, AuxiliaryDigitalTraces)):
            if "aux_traces" not in event:
                event["aux_traces"] = {}
            event["aux_traces"][o.header.id] = o.parse()

    return event
