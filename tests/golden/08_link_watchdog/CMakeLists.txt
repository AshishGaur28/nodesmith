cmake_minimum_required(VERSION 3.16)
project(link_watchdog_node)

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
find_package(std_msgs REQUIRED)

set(NODE_SOURCES
  src/main.cpp
  src/app.cpp
  src/link_watchdog_node_node.cpp
  src/interfaces.cpp
  src/parameters.cpp
  src/diagnostics.cpp)

add_executable(link_watchdog_node ${NODE_SOURCES})
target_include_directories(link_watchdog_node PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>)
ament_target_dependencies(link_watchdog_node
  diagnostic_msgs
  rcl_interfaces
  rclcpp
  std_msgs
  )

install(TARGETS link_watchdog_node DESTINATION lib/${PROJECT_NAME})
install(DIRECTORY include/ DESTINATION include)
install(DIRECTORY launch config DESTINATION share/${PROJECT_NAME})

ament_package()
