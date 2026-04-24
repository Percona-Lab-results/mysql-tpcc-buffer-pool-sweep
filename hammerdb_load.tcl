#!/usr/bin/tclsh
# HammerDB 5.0 TPC-C schema build for MySQL 8.4.
# buildschema blocks until all loader virtual users finish (internal
# _waittocomplete) and then creates the TPC-C stored procedures, so no
# external wait is needed.
#
# Run via: /opt/HammerDB-5.0/hammerdbcli auto /root/benchmarks/hammerdb_load.tcl

puts "SETTING CONFIGURATION"
dbset db mysql
dbset bm TPC-C

diset connection mysql_host 127.0.0.1
diset connection mysql_port 3306
diset connection mysql_socket null

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
diset tpcc mysql_storage_engine innodb
diset tpcc mysql_partition true

# Scale: warehouses and loader virtual users used to build the schema.
# 1000 warehouses × ~100 MB = ~100 GB datadir — matches the backup the
# sweep restores from. 64 loader VUs parallelises the bulk insert phase.
diset tpcc mysql_count_ware 1000
diset tpcc mysql_num_vu 64

puts "SCHEMA BUILD STARTED"
set ret [buildschema]
puts "SCHEMA BUILD COMPLETED: $ret"
