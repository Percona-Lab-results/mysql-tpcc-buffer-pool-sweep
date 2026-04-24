#!/usr/bin/tclsh
# HammerDB 5.0 TPC-C timed run for MariaDB. Schema- and API-wise the driver
# is independent from the mysql one, hence the separate `maria_*` keys.
#
# Env overrides (same names as the MySQL version so the sweep can be
# profile-agnostic):
#   HDB_NUM_VU     number of virtual users (default: 64)
#   HDB_RAMPUP     rampup minutes (default: 10)
#   HDB_DURATION   measurement minutes (default: 60)
#   HDB_TC_RATE    transaction-counter refresh seconds (default: 1)
#   HDB_OUTFILE    path to write jobid + result summary

set num_vu   [expr {[info exists ::env(HDB_NUM_VU)]   ? $::env(HDB_NUM_VU)   : 64}]
set rampup   [expr {[info exists ::env(HDB_RAMPUP)]   ? $::env(HDB_RAMPUP)   : 10}]
set duration [expr {[info exists ::env(HDB_DURATION)] ? $::env(HDB_DURATION) : 60}]
set tc_rate  [expr {[info exists ::env(HDB_TC_RATE)]  ? $::env(HDB_TC_RATE)  : 1}]
set outfile  [expr {[info exists ::env(HDB_OUTFILE)]  ? $::env(HDB_OUTFILE)  : "/tmp/hammerdb_run.out"}]

puts "SETTING CONFIGURATION (maria num_vu=$num_vu rampup=${rampup}m duration=${duration}m tc_rate=${tc_rate}s)"
dbset db maria
dbset bm TPC-C

# maria_socket=null forces TCP when host is 127.0.0.1/localhost (same rule as
# the mysql driver). Container's unix socket is not reachable from the host.
diset connection maria_host 127.0.0.1
diset connection maria_port 3306
diset connection maria_socket null

diset tpcc maria_user root
diset tpcc maria_pass rootpassword
diset tpcc maria_dbase tpcc
diset tpcc maria_driver timed
diset tpcc maria_rampup $rampup
diset tpcc maria_duration $duration
diset tpcc maria_allwarehouse true
diset tpcc maria_timeprofile false
diset tpcc maria_num_vu $num_vu

tcset refreshrate $tc_rate
tcset logtotemp 1
tcset unique 1
tcset timestamps 1

loadscript
puts "TEST STARTED"
vuset vu $num_vu
vucreate
tcstart
tcstatus
set vurun_ret [vurun]
vudestroy
tcstop
if {[regexp {jobid=([0-9A-Fa-f]+)} $vurun_ret -> jobid]} {
    puts "TEST COMPLETE jobid=$jobid"
} else {
    set jobid ""
    puts "TEST COMPLETE — could not parse jobid from: $vurun_ret"
}

set fh [open $outfile w]
puts $fh "jobid=$jobid"
puts $fh "num_vu=$num_vu"
puts $fh "rampup_min=$rampup"
puts $fh "duration_min=$duration"
puts $fh "tc_refresh_sec=$tc_rate"
puts $fh "---"
if {$jobid ne ""} {
    puts $fh "TRANSACTION RESPONSE TIMES"
    if {[catch {job $jobid timing} res]} { puts $fh "ERROR: $res" } else { puts $fh $res }
    puts $fh "TRANSACTION COUNT"
    if {[catch {job $jobid tcount} res]} { puts $fh "ERROR: $res" } else { puts $fh $res }
    puts $fh "HAMMERDB RESULT"
    if {[catch {job $jobid result} res]} { puts $fh "ERROR: $res" } else { puts $fh $res }
} else {
    puts $fh "no jobid — vurun returned: $vurun_ret"
}
close $fh
puts "RESULTS WRITTEN TO $outfile"
