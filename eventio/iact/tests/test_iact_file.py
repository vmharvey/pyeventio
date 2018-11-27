import eventio
import pkg_resources
from os import path

from pytest import approx


testfile = pkg_resources.resource_filename(
    'eventio', path.join('resources', 'one_shower.dat')
)
testfile_reuse = pkg_resources.resource_filename(
    'eventio', path.join('resources', '3_gammas_reuse_5.dat')
)
testfile_two_telescopes = pkg_resources.resource_filename(
    'eventio', path.join('resources', 'two_telescopes.dat')
)


def test_file_open():
    eventio.IACTFile(testfile)


def test_n_events():
    ''' tests if the number of events in the testfile is 1 '''
    with eventio.IACTFile(testfile) as f:
        for i, event in enumerate(f):
            pass

    assert i == 0
    assert event.event_id == 1
    assert event.reuse == 1


def test_read_run_header():
    with eventio.IACTFile(testfile) as f:
        assert hasattr(f, 'header')
        assert f.header.energy_range[0] == approx(5.0)
        assert f.header.energy_range[1] == approx(100.0)
        assert f.header.energy_slope == approx(-2.7)


def test_run_end_block():
    with eventio.IACTFile(testfile) as f:
        # looping through all events will fill the run_end block in the end
        for event in f:
            pass

    assert hasattr(f, 'run_end')


def test_read_input_card():
    with eventio.IACTFile(testfile) as f:
        assert hasattr(f, 'input_card')


def test_read_telescopes():
    with eventio.IACTFile(testfile) as f:
        assert f.n_telescopes == 1
        assert hasattr(f, 'telescope_positions')
        assert f.telescope_positions['x'][0] == approx(0)


def test_read_telescopes_2():
    with eventio.IACTFile(testfile_two_telescopes) as f:
        assert f.n_telescopes == 2
        assert hasattr(f, 'telescope_positions')
        assert f.telescope_positions['x'][1] == approx(5000)


def test_iterating():
    with eventio.IACTFile(testfile) as f:
        for event in f:
            assert isinstance(event, eventio.iact.CORSIKAEvent)


def test_bunches():
    with eventio.IACTFile(testfile) as f:

        event = next(iter(f))
        columns = ('x', 'y', 'cx', 'cy', 'time', 'zem', 'photons', 'lambda', 'scattered')

        assert event.photon_bunches[0].shape == (382, )
        assert event.photon_bunches[0].dtype.names == columns


def test_bunches_2():
    columns = ('x', 'y', 'cx', 'cy', 'time', 'zem', 'photons', 'lambda', 'scattered')

    with eventio.IACTFile(testfile_two_telescopes) as f:
        for event in f:
            assert len(event.photon_bunches) == 2
            assert event.photon_bunches[1].dtype.names == columns


def test_event_header():
    with eventio.IACTFile(testfile) as f:
        event = next(iter(f))

        assert hasattr(event, 'header')
        assert event.header.event_id == 1
        assert event.header.zenith_angle == approx(0.0)
        assert event.header.azimuth_angle == approx(0.0)
        assert event.header.total_energy == approx(9.3249321)


def test_event_with_reuse():
    with eventio.IACTFile(testfile_reuse) as f:
        for i, e in enumerate(f):
            assert e.event_id == i // 5 + 1
            assert e.reuse == (i % 5) + 1
