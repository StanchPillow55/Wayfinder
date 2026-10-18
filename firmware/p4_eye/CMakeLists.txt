# Wayfinder P4X-EYE adapter SCAFFOLD. SOURCE ONLY until a board enumerates.
# Not built, not flashed. Target selection is left to idf.py:
#   idf.py set-target esp32p4
cmake_minimum_required(VERSION 3.16)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(wayfinder_p4_eye)
