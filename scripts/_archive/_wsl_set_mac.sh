#!/usr/bin/env bash
# Set WSL2 eth0 MAC to match Xilinx license HOSTID (6c:02:e0:44:03:20)
# Called at WSL boot via /etc/wsl.conf [boot] command
ip link set eth0 address 6c:02:e0:44:03:20 2>/dev/null || true
