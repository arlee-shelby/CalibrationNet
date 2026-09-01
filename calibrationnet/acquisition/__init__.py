"""The files here acquire and ingest data that is not produced by this repo.
Specifically, the slow-controls metadata (from two separate databases, both on the slow-controls
computer) and the raw hdf5 files (i.e. to get waveform data and the board-channel-pixel map). One module 
per data source:

- slow_controls:       run timing/metadata from the Nab_SlowControl database
- epics_controls:      positions + run settings from the EPICS archive ("Test" database)
- run_metadata:        composes the two above into runs + run_segments rows
- board_channels:      board-channel -> pixel map, read from the raw hdf5 files
- waveforms:           waveform reading from the raw hdf5 files (feeds the trap filter)
- trap_filter:         trap-filter output handling and ingestion
- electronics_mapping: pixel -> preamp/FET map from the transcribed CSV
"""
