#!/usr/bin/tclsh
# HammerDB 5.0 TPC-C schema build for SeekDB (OceanBase-compatible,
# MySQL wire protocol on port 2881).
#
# Loads tables + data but deliberately skips the stored-procedure phase:
# SeekDB doesn't currently support the CREATE PROCEDURE bodies HammerDB
# emits for MySQL. `mysql_no_stored_procs true` tells the *driver* to use
# inline SQL instead of CALL at run time; HammerDB still attempts to
# create the procs during build, so errors there are expected and
# non-fatal for the data load itself.
#
# Run via:
#   /opt/HammerDB-5.0/hammerdbcli auto /root/benchmarks/hammerdb_load_seekdb.tcl

puts "SETTING CONFIGURATION (seekdb build)"
dbset db mysql
dbset bm TPC-C

# SeekDB defaults: MySQL 5.7-compat wire on 2881.
diset connection mysql_host 127.0.0.1
diset connection mysql_port 2881
diset connection mysql_socket null
diset connection mysql_ssl false

diset tpcc mysql_user root
diset tpcc mysql_pass password
diset tpcc mysql_dbase tpcc
diset tpcc mysql_storage_engine innodb

# Partitioning and native_password-style features aren't meaningful here;
# leave partition off so the schema is minimal.
diset tpcc mysql_partition false

# No stored procedures — the run-time driver will embed SQL inline.
diset tpcc mysql_no_stored_procs true

# Scale: 1000 warehouses to match the other profiles.
diset tpcc mysql_count_ware 1000
# 64 loader VUs caused "Over tenant memory limits" errors during the
# order_line phase — too many concurrent memtable writers. 16 VUs keeps
# memory pressure manageable at the cost of slower load.
diset tpcc mysql_num_vu 16

puts "SCHEMA BUILD STARTED"
set ret [buildschema]
puts "SCHEMA BUILD COMPLETED: $ret"
