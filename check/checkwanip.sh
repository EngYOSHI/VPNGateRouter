#!/bin/bash
while [ true ]
do
  date_str=$(date '+%Y-%m-%d %H:%M:%S')
  cmd=$(curl -m 1.5 -s inet-ip.info)
  if [ -z "$cmd" ]; then
    cmd="Time out"
  fi
  echo "$date_str $cmd"
  sleep 1
done
