cmake_minimum_required(VERSION 3.16)
project(imu_filter_node)

if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 17)
  set(CMAKE_CXX_STANDARD_REQUIRED ON)
endif()

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  # No fused multiply-add: float results must be bit-identical on every target (SPEC-02 §6).
  add_compile_options(-Wall -Wextra -ffp-contract=off)
endif()

find_package(ament_cmake REQUIRED)
find_package(diagnostic_msgs REQUIRED)
find_package(rcl_interfaces REQUIRED)
find_package(rclcpp REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(std_msgs REQUIRED)

set(NODE_SOURCES
  src/main.cpp
  src/app.cpp
  src/imu_filter_node_node.cpp
  src/interfaces.cpp
  src/parameters.cpp
  src/diagnostics.cpp)

add_executable(imu_filter_node ${NODE_SOURCES})
target_include_directories(imu_filter_node PRIVATE include)
ament_target_dependencies(imu_filter_node
  diagnostic_msgs
  rcl_interfaces
  rclcpp
  sensor_msgs
  std_msgs
  )

install(TARGETS imu_filter_node DESTINATION lib/${PROJECT_NAME})
install(DIRECTORY launch config DESTINATION share/${PROJECT_NAME})

ament_package()
