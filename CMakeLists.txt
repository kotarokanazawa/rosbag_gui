cmake_minimum_required(VERSION 3.0.2)
project(rosbag_gui)

find_package(catkin REQUIRED)

catkin_package()

catkin_install_python(PROGRAMS
  scripts/rosbag_gui.py
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)

install(DIRECTORY launch presets rosbag
  DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION}
)
