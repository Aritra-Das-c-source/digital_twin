cmake_minimum_required(VERSION 3.20)

project(DigitalTwinSimulation)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(simulation
    src/main.cpp
    src/Simulation.cpp
)