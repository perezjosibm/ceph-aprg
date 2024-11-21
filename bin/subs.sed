#!/usr/bin/sed -f
#Script to replace the _dat file names in th e.gnuplot script
s/TEST_RUN//g
s/TEST_TITLE//g
s/DEFAULT_DAT//g
s/OSD_BAL_DAT//g
s/SOCKET_BAL_DAT//g
