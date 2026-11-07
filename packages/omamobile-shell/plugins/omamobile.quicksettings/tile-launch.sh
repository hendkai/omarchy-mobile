#!/bin/sh
# SPDX-License-Identifier: MIT
# tile-launch.sh TILE: open a car-app tile's surface. The quick-settings sheet
# puts itself away first (QuickSettings.qml), so the surface opens on the
# desktop. The CLI tiles run their command in a terminal and wait for Enter,
# as the notfall desktop entry does; the audiobook tile opens the web client.
#   charge      Ladeplaner: asks for the position, then the stations in range
#   trips       Fahrtenbuch: asks for the month (empty: this month), exports it
#   parking     Parkplatz: the last parked-at point
#   audiobooks  Audiobookshelf web client (audiobookshelf-open)
# OMAMOBILE_TERMINAL picks the terminal (default foot, the phone's).
set -u

tile=${1:-}
term=${OMAMOBILE_TERMINAL:-foot}

# The commands are fixed strings; nothing the sheet passes reaches a shell.
case $tile in
  charge)
    cmd='printf "Standort (Breitengrad Laengengrad): "; read -r lat lon; chargeplanner "$lat" "$lon"' ;;
  trips)
    cmd='printf "Monat (JJJJ-MM, leer = dieser Monat): "; read -r m; fahrtenbuch export "${m:-$(date +%Y-%m)}"' ;;
  parking)
    cmd='parkreminder show' ;;
  audiobooks)
    exec audiobookshelf-open ;;
  *)
    echo "usage: tile-launch.sh charge|trips|parking|audiobooks" >&2
    exit 2 ;;
esac

exec "$term" sh -c "$cmd; echo; printf 'Enter zum Schliessen '; read -r _" tile-launch
