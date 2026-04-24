#!/usr/bin/tclsh
# HammerDB 5.0 TPC-C schema build for MariaDB. buildschema blocks until
# loader VUs finish and creates the TPC-C stored procedures — no explicit
# wait needed.

puts "SETTING CONFIGURATION (maria build)"
dbset db maria
dbset bm TPC-C

diset connection maria_host 127.0.0.1
diset connection maria_port 3306
diset connection maria_socket null

diset tpcc maria_user root
diset tpcc maria_pass rootpassword
diset tpcc maria_dbase tpcc
diset tpcc maria_storage_engine innodb
diset tpcc maria_partition true

diset tpcc maria_count_ware 1000
diset tpcc maria_num_vu 64

puts "SCHEMA BUILD STARTED"
set ret [buildschema]
puts "SCHEMA BUILD COMPLETED: $ret"
