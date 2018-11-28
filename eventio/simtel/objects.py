''' Implementations of the simtel_array EventIO object types '''
import numpy as np
from ..base import EventIOObject
from ..tools import read_ints, read_eventio_string, read_from, read_array


class History(EventIOObject):
    eventio_type = 70


class HistoryCommandLine(EventIOObject):
    eventio_type = 71

    def __init__(self, header, parent):
        super().__init__(header, parent)
        self.timestamp, = read_ints(1, self)

    def parse_data_field(self):
        self.seek(4)  # skip the int, we already read in init
        return read_eventio_string(self)


class HistoryConfig(EventIOObject):
    eventio_type = 72

    def __init__(self, header, parent):
        super().__init__(header, parent)
        self.timestamp, = read_ints(1, self)

    def parse_data_field(self):
        self.seek(4)  # skip the int, we already read in init
        return read_eventio_string(self)


class SimTelRunHeader(EventIOObject):
    eventio_type = 2000
    from .runheader_dtypes import (
        runheader_dtype_part1,
        runheader_dtype_part2
    )

    def __init__(self, header, parent):
        super().__init__(header, parent)
        self.run_id = self.header.id

    def parse_data_field(self):
        '''See write_hess_runheader l. 184 io_hess.c '''
        self.seek(0)
        dt1 = SimTelRunHeader.runheader_dtype_part1

        part1 = read_array(self, dtype=dt1, count=1)[0]
        dt2 = SimTelRunHeader.runheader_dtype_part2(part1['n_telescopes'])
        part2 = read_array(self, dtype=dt2, count=1)[0]

        # rest is two null-terminated strings
        target = read_eventio_string(self)
        observer = read_eventio_string(self)

        result = dict(zip(part1.dtype.names, part1))
        result.update(dict(zip(part2.dtype.names, part2)))
        result['target'] = target
        result['observer'] = observer

        return result


class SimTelMCRunHeader(EventIOObject):
    eventio_type = 2001
    from .mc_runheader_dtypes import mc_runheader_dtype_map

    def parse_data_field(self):
        ''' '''
        self.seek(0)

        if self.header.version not in self.mc_runheader_dtype_map:
            raise IOError(
                'Unsupported version of MCRunHeader: {}'.format(self.header.version)
            )

        header_type = self.mc_runheader_dtype_map[self.header.version]
        return read_array(self, dtype=header_type, count=1).view(np.recarray)[0]


class SimTelCamSettings(EventIOObject):
    eventio_type = 2002

    def __init__(self, header, parent):
        super().__init__(header, parent)
        self.telescope_id = header.id

    def parse_data_field(self):
        n_pixels, = read_from('<i', self)
        focal_length, = read_from('<f', self)
        pixel_x = read_array(self, count=n_pixels, dtype='float32')
        pixel_y = read_array(self, count=n_pixels, dtype='float32')

        return {
            'telescope_id': self.telescope_id,
            'n_pixels': n_pixels,
            'focal_length': focal_length,
            'pixel_x': pixel_x,
            'pixel_y': pixel_y,
        }

    def __repr__(self):
        return '{}[{}](telescope_id={}, size={}, first_byte={})'.format(
            self.__class__.__name__,
            self.eventio_type,
            self.telescope_id,
            self.header.length,
            self.header.data_field_first_byte
        )


class SimTelCamOrgan(EventIOObject):
    eventio_type = 2003


class SimTelPixelset(EventIOObject):
    eventio_type = 2004


class SimTelPixelDisable(EventIOObject):
    eventio_type = 2005

    def __init__(self, header, parent):
        super().__init__(header, parent)
        self.telescope_id = header.id

    def parse_data_field(self):
        ''' '''
        self.seek(0)

        if not self.header.version == 0:
            raise IOError(
                (
                    'Unsupported version of {}: only version 0 is supported '
                    'this is version {}'
                ).format(self.__class__.__name__, self.header.version)
            )

        num_trig_disabled, = read_from('<i', self)
        trigger_disabled = read_array(
            self,
            count=num_trig_disabled,
            dtype='i4'
        )
        num_HV_disabled, = read_from('<i', self)
        HV_disabled = read_array(self, count=num_trig_disabled, dtype='i4')

        return {
            'telescope_id': self.telescope_id,
            'num_trig_disabled': num_trig_disabled,
            'trigger_disabled': trigger_disabled,
            'num_HV_disabled': num_HV_disabled,
            'HV_disabled': HV_disabled,
        }

class SimTelCamsoftset(EventIOObject):
    eventio_type = 2006


class SimTelPointingCor(EventIOObject):
    eventio_type = 2007


class SimTelTrackSet(EventIOObject):
    eventio_type = 2008


class SimTelCentEvent(EventIOObject):
    eventio_type = 2009


class SimTelTrackEvent(EventIOObject):
    '''Tracking information for a simtel telescope event
    This has no clear type number, since
    Konrad Bernlöhr decided to encode the telescope id into
    the container type as 2100 + tel_id % 100 + 1000 * (tel_id // 100)

    So a container with type 2105 belongs to tel_id 5, 3105 to 105
    '''
    eventio_type = None

    def __init__(self, header, parent):
        self.eventio_type = header.type
        super().__init__(header, parent)
        self.telescope_id = self.type_to_telid(header.type)
        if not self.id_to_telid(header.id) == self.telescope_id:
            raise ValueError('Telescope IDs in type and header do not match')

        self.has_raw = bool(header.id & 0x100)
        self.has_cor = bool(header.id & 0x200)

    def parse_data_field(self):
        dt = []
        if self.has_raw:
            dt.extend([('azimuth_raw', '<f4'), ('altitude_raw', '<f4')])
        if self.has_cor:
            dt.extend([('azimuth_cor', '<f4'), ('altitude_cor', '<f4')])
        return read_array(self, count=1, dtype=dt)[0]

    @staticmethod
    def id_to_telid(eventio_id):
        '''See io_hess.c, l. 2519'''
        return (eventio_id & 0xff) | ((eventio_id & 0x3f000000) >> 16)

    @staticmethod
    def type_to_telid(eventio_type):
        base = eventio_type - 2100
        return 100 * (base // 1000) + base % 1000

    @staticmethod
    def telid_to_type(telescope_id):
        return 2100 + telescope_id % 100 + 1000 * (telescope_id // 100)

    def __repr__(self):
        return '{}[{}](telescope_id={}, size={}, first_byte={})'.format(
            self.__class__.__name__,
            self.eventio_type,
            self.telescope_id,
            self.header.length,
            self.header.data_field_first_byte
        )


class SimTelTelEvent(EventIOObject):
    '''A simtel telescope event
    This has no clear type number, since
    Konrad Bernlöhr decided to encode the telescope id into
    the container type as 2200 + tel_id % 100 + 1000 * (tel_id // 100)

    So a container with type 2205 belongs to tel_id 5, 3205 to 105
    '''
    eventio_type = None

    def __init__(self, header, parent):
        self.eventio_type = header.type
        super().__init__(header, parent)
        self.telescope_id = self.type_to_telid(header.type)
        self.global_count = header.id

    @staticmethod
    def type_to_telid(eventio_type):
        base = eventio_type - 2200
        return 100 * (base // 1000) + base % 1000

    @staticmethod
    def telid_to_type(telescope_id):
        return 2200 + telescope_id % 100 + 1000 * (telescope_id // 100)

    def __repr__(self):
        return '{}[{}](telescope_id={}, size={}, first_byte={})'.format(
            self.__class__.__name__,
            self.eventio_type,
            self.telescope_id,
            self.header.length,
            self.header.data_field_first_byte
        )


class SimTelEvent(EventIOObject):
    eventio_type = 2010


class SimTelTelEvtHead(EventIOObject):
    eventio_type = 2011


class SimTelTelADCSum(EventIOObject):
    eventio_type = 2012


class SimTelTelADCSamp(EventIOObject):
    eventio_type = 2013


class SimTelTelImage(EventIOObject):
    eventio_type = 2014


class SimTelShower(EventIOObject):
    eventio_type = 2015


class SimTelPixelTiming(EventIOObject):
    eventio_type = 2016


class SimTelPixelCalib(EventIOObject):
    eventio_type = 2017


class SimTelMCShower(EventIOObject):
    eventio_type = 2020


class SimTelMCEvent(EventIOObject):
    eventio_type = 2021


class SimTelTelMoni(EventIOObject):
    eventio_type = 2022


class SimTelLasCal(EventIOObject):
    eventio_type = 2023


class SimTelRunStat(EventIOObject):
    eventio_type = 2024


class SimTelMCRunStat(EventIOObject):
    eventio_type = 2025


class SimTelMCPeSum(EventIOObject):
    eventio_type = 2026


class SimTelPixelList(EventIOObject):
    eventio_type = 2027


class SimTelCalibEvent(EventIOObject):
    eventio_type = 2028
