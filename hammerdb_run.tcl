#!/usr/bin/tclsh
# HammerDB 5.0 TPC-C timed run for MySQL.
# Tuned for: 10 min rampup, 60 min measurement, 1-second transaction counter.
# Environment overrides:
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

puts "SETTING CONFIGURATION (num_vu=$num_vu rampup=${rampup}m duration=${duration}m tc_rate=${tc_rate}s)"
dbset db mysql
dbset bm TPC-C

diset connection mysql_host 127.0.0.1
diset connection mysql_port 3306
diset connection mysql_socket null

# SSL is required for MySQL 9.x (caching_sha2_password, no native plugin).
# Optional for 8.x — enabled when HDB_SSL=true.
set hdb_ssl [expr {[info exists ::env(HDB_SSL)] ? $::env(HDB_SSL) : "false"}]
diset connection mysql_ssl $hdb_ssl
if {$hdb_ssl eq "true"} {
    set capath [expr {[info exists ::env(HDB_SSL_CAPATH)] ? $::env(HDB_SSL_CAPATH) : "/root/benchmarks/mysql-certs"}]
    set ca     [expr {[info exists ::env(HDB_SSL_CA)]     ? $::env(HDB_SSL_CA)     : "ca.pem"}]
    diset connection mysql_ssl_linux_capath $capath
    diset connection mysql_ssl_ca $ca
    diset connection mysql_ssl_cert ""
    diset connection mysql_ssl_key ""
    diset connection mysql_ssl_two_way false
}

diset tpcc mysql_user root
diset tpcc mysql_pass rootpassword
diset tpcc mysql_dbase tpcc
diset tpcc mysql_driver timed
diset tpcc mysql_rampup $rampup
diset tpcc mysql_duration $duration
diset tpcc mysql_allwarehouse true
diset tpcc mysql_timeprofile false
diset tpcc mysql_num_vu $num_vu
# HDB_NO_STORED_PROCS=true makes the run-time driver embed SQL inline
# instead of CALL-ing stored procedures. Default false — schemas loaded
# via hammerdb_load.tcl contain NEWORD/PAYMENT/DELIVERY/OSTAT/SLEV.
set hdb_no_sp [expr {[info exists ::env(HDB_NO_STORED_PROCS)] ? $::env(HDB_NO_STORED_PROCS) : "false"}]
diset tpcc mysql_no_stored_procs $hdb_no_sp

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
# vurun returns "Benchmark Run jobid=<hex>" (or "Benchmark Run (No jobid)").
# The `job` command needs the bare id, so split on '=' and take the tail.
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
