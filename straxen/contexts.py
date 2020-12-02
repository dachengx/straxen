from immutabledict import immutabledict
import strax
import straxen
import numpy as np


common_opts = dict(
    register_all=[
        straxen.event_processing,
        straxen.double_scatter],
    # Register all peak/pulse processing by hand as 1T does not need to have
    # the high-energy plugins.
    register=[
        straxen.PulseProcessing,
        straxen.Peaklets,
        straxen.PeakletClassification,
        straxen.MergedS2s,
        straxen.Peaks,
        straxen.PeakBasics,
        straxen.PeakPositions,
        straxen.PeakProximity],
    check_available=('raw_records', 'peak_basics'),
    store_run_fields=(
        'name', 'number',
        'start', 'end', 'livetime', 'mode'))

xnt_common_config = dict(
    n_nveto_pmts=120,
    n_tpc_pmts=straxen.n_tpc_pmts,
    n_top_pmts=straxen.n_top_pmts,
    gain_model=("CMT_model", ("to_pe_model", "ONLINE")),
    channel_map=immutabledict(
        # (Minimum channel, maximum channel)
        # Channels must be listed in a ascending order!
        tpc=(0, 493),
        he=(500, 752),  # high energy
        aqmon=(790, 807),
        aqmon_nv=(808, 815),  # nveto acquisition monitor
        tpc_blank=(999, 999),
        mv=(1000, 1083),
        mv_blank=(1999, 1999),
        nveto=(2000, 2119),
        nveto_blank=(2999, 2999)),
    nn_architecture=straxen.aux_repo + 'f0df03e1f45b5bdd9be364c5caefdaf3c74e044e/fax_files/mlp_model.json',
    nn_weights=straxen.aux_repo + 'f0df03e1f45b5bdd9be364c5caefdaf3c74e044e/fax_files/mlp_model.h5', 
    # Clustering/classification parameters
    s1_max_rise_time=100,
)

# Plugins in these files have nT plugins, E.g. in pulse&peak(let)
# processing there are plugins for High Energy plugins. Therefore do not
# st.register_all in 1T contexts.
have_nT_plugins = [straxen.nveto_recorder,
                   straxen.nveto_pulse_processing,
                   straxen.nveto_hitlets,
                   straxen.acqmon_processing,
                   straxen.pulse_processing,
                   straxen.peaklet_processing,
                   straxen.peak_processing,
                   straxen.online_monitor,
                   ]

##
# XENONnT
##


def xenonnt_online(output_folder='./strax_data',
                   we_are_the_daq=False,
                   _minimum_run_number=7157,
                   _database_init=True,
                   **kwargs):
    """XENONnT online processing and analysis"""
    context_options = {
        **straxen.contexts.common_opts,
        **kwargs}

    st = strax.Context(
        config=straxen.contexts.xnt_common_config,
        **context_options)
    st.register_all(have_nT_plugins)
    st.register([straxen.DAQReader, straxen.LEDCalibration])

    st.storage = [straxen.RunDB(
        readonly=not we_are_the_daq,
        minimum_run_number=_minimum_run_number,
        runid_field='number',
        new_data_path=output_folder,
        rucio_path='/dali/lgrandi/rucio/')
        ] if _database_init else []
    if not we_are_the_daq:
        st.storage += [
            strax.DataDirectory(
                '/dali/lgrandi/xenonnt/raw',
                readonly=True,
                take_only=straxen.DAQReader.provides),
            strax.DataDirectory(
                '/dali/lgrandi/xenonnt/processed',
                readonly=True)]
        if output_folder:
            st.storage.append(
                strax.DataDirectory(output_folder))

        st.context_config['forbid_creation_of'] = straxen.daqreader.DAQReader.provides + ('records',)
    # Only the online monitor backend for the DAQ
    elif _database_init:
        st.storage += [straxen.OnlineMonitor(
            readonly=not we_are_the_daq,
            take_only=('veto_intervals',
                       'online_monitor',))]

    # Remap the data if it is before channel swap (because of wrongly cabled
    # signal cable connectors) These are runs older than run 8797. Runs
    # newer than 8796 are not affected. See:
    # https://github.com/XENONnT/straxen/pull/166 and
    # https://xe1t-wiki.lngs.infn.it/doku.php?id=xenon:xenonnt:dsg:daq:sector_swap
    st.set_context_config({'apply_data_function': (straxen.common.remap_old,)})
    return st


def xenonnt_led(**kwargs):
    st = xenonnt_online(**kwargs)
    st.context_config['check_available'] = ('raw_records', 'led_calibration')
    # Return a new context with only raw_records and led_calibration registered
    st = st.new_context(
        replace=True,
        config=st.config,
        storage=st.storage,
        **st.context_config)
    st.register([straxen.DAQReader, straxen.LEDCalibration])
    return st


# This gain model is a temporary solution until we have a nice stable one
def xenonnt_simulation(output_folder='./strax_data'):
    import wfsim
    xnt_common_config['gain_model'] = ('to_pe_per_run',
                                       straxen.aux_repo + '58e615f99a4a6b15e97b12951c510de91ce06045/fax_files/to_pe_nt.npy')
    st = strax.Context(
        storage=strax.DataDirectory(output_folder),
        config=dict(detector='XENONnT',
                    fax_config=straxen.aux_repo + '4e71b8a2446af772c83a8600adc77c0c3b7e54d1/fax_files/fax_config_nt.json',
                    check_raw_record_overlaps=False,
                    **straxen.contexts.xnt_common_config,
                    ),
        **straxen.contexts.common_opts)
    st.register(wfsim.RawRecordsFromFaxNT)
    return st


def xenonnt_temporary_five_pmts(selected_pmts=(257, 313, 355, 416, 455),
                                gains_from_run='010523',
                                **kwargs):
    """Temporary context for PMTs 257, 313, 355, 416, 455"""
    # Start from the online context
    st = xenonnt_online(**kwargs)
    gain_key = f'five_pmts_{gains_from_run}'

    # Get the correct gains from CMT
    cmt_gains = straxen.get_to_pe(gains_from_run, st.config['gain_model'], straxen.n_tpc_pmts)
    pmt_list = list(selected_pmts)

    # Let's set all the gains to zero except the gains of the PMTs we just specified
    five_gains = np.zeros(straxen.n_tpc_pmts)
    five_gains[pmt_list] = cmt_gains[pmt_list]
    straxen.FIXED_TO_PE[gain_key] = five_gains

    # Create a new context and set the gains to the values we have just created
    st_five_pmts = st.new_context()
    st_five_pmts.set_config({'gain_model': ('to_pe_constant', gain_key),
                             'peak_min_pmts': 2,
                             'peaklet_gap_threshold': 300,
                             })

    return st_five_pmts


def xenonnt_initial_commissioning(*args, **kwargs):
    raise ValueError(
        'Use xenonnt_online. See' 
        'https://xe1t-wiki.lngs.infn.it/doku.php?id=xenon:xenonnt:analysis:commissioning:straxen_contexts#update_09_nov_20')

##
# XENON1T
##


x1t_context_config = {
    **common_opts,
    **dict(
        check_available=('raw_records', 'records', 'peaklets',
                         'events', 'event_info'),
        free_options=('channel_map',),
        store_run_fields=tuple(
            [x for x in common_opts['store_run_fields'] if x != 'mode']
            + ['trigger.events_built', 'reader.ini.name']))}

x1t_common_config = dict(
    check_raw_record_overlaps=False,
    allow_sloppy_chunking=True,
    n_tpc_pmts=248,
    n_top_pmts=127,
    channel_map=immutabledict(
        # (Minimum channel, maximum channel)
        tpc=(0, 247),
        diagnostic=(248, 253),
        aqmon=(254, 999)),
    hev_gain_model=('to_pe_per_run',
                    'https://raw.githubusercontent.com/XENONnT/strax_auxiliary_files/master/to_pe.npy'),
    gain_model=('to_pe_per_run',
                'https://raw.githubusercontent.com/XENONnT/strax_auxiliary_files/master/to_pe.npy'),
    pmt_pulse_filter=(
        0.012, -0.119,
        2.435, -1.271, 0.357, -0.174, -0., -0.036,
        -0.028, -0.019, -0.025, -0.013, -0.03, -0.039,
        -0.005, -0.019, -0.012, -0.015, -0.029, 0.024,
        -0.007, 0.007, -0.001, 0.005, -0.002, 0.004, -0.002),
    tail_veto_threshold=int(1e5),
    # Smaller right extension since we applied the filter
    peak_right_extension=30,
    peak_min_pmts=2,
    save_outside_hits=(3, 3),
    hit_min_amplitude='XENON1T_SR1',
    peak_split_gof_threshold=(
        None,  # Reserved
        ((0.5, 1), (3.5, 0.25)),
        ((2, 1), (4.5, 0.4))))


def demo():
    """Return strax context used in the straxen demo notebook"""
    straxen.download_test_data()

    st = strax.Context(
        storage=[strax.DataDirectory('./strax_data'),
                 strax.DataDirectory('./strax_test_data',
                                     deep_scan=True,
                                     provide_run_metadata=True,
                                     readonly=True)],
        forbid_creation_of=straxen.daqreader.DAQReader.provides,
        config=dict(**x1t_common_config),
        **x1t_context_config)
    st.register(straxen.RecordsFromPax)
    return st


def fake_daq():
    """Context for processing fake DAQ data in the current directory"""
    st = strax.Context(
        storage=[strax.DataDirectory('./strax_data'),
                 # Fake DAQ puts run doc JSON in same folder:
                 strax.DataDirectory('./from_fake_daq',
                                     provide_run_metadata=True,
                                     readonly=True)],
        config=dict(daq_input_dir='./from_fake_daq',
                    daq_chunk_duration=int(2e9),
                    daq_compressor='lz4',
                    n_readout_threads=8,
                    daq_overlap_chunk_duration=int(2e8),
                    **x1t_common_config),
        **x1t_context_config)
    st.register(straxen.Fake1TDAQReader)
    return st


def xenon1t_dali(output_folder='./strax_data', build_lowlevel=False, **kwargs):
    context_options = {
        **x1t_context_config,
        **kwargs}

    st = strax.Context(
        storage=[
            strax.DataDirectory(
                '/dali/lgrandi/xenon1t/strax_converted/raw',
                take_only='raw_records',
                provide_run_metadata=True,
                readonly=True),
            strax.DataDirectory(
                '/dali/lgrandi/xenon1t/strax_converted/processed',
                readonly=True),
            strax.DataDirectory(output_folder)],
        config=dict(**x1t_common_config),
        # When asking for runs that don't exist, throw an error rather than
        # starting the pax converter
        forbid_creation_of=(
            straxen.daqreader.DAQReader.provides if build_lowlevel
            else straxen.daqreader.DAQReader.provides + ('records', 'peaklets')),
        **context_options)
    st.register(straxen.RecordsFromPax)
    return st


def xenon1t_led(**kwargs):
    st = xenon1t_dali(**kwargs)
    st.context_config['check_available'] = ('raw_records', 'led_calibration')
    # Return a new context with only raw_records and led_calibration registered
    st = st.new_context(
        replace=True,
        config=st.config,
        storage=st.storage,
        **st.context_config)
    st.register([straxen.RecordsFromPax, straxen.LEDCalibration])
    return st


def xenon1t_simulation(output_folder='./strax_data'):
    import wfsim
    st = strax.Context(
        storage=strax.DataDirectory(output_folder),
        config=dict(
            fax_config=straxen.aux_repo + '1c5793b7d6c1fdb7f99a67926ee3c16dd3aa944f/fax_files/fax_config_1t.json',
            detector='XENON1T',
            check_raw_record_overlaps=False,
            **straxen.contexts.x1t_common_config),
        **straxen.contexts.common_opts)
    st.register(wfsim.RawRecordsFromFax1T)
    return st
