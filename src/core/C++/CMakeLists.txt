cmake_minimum_required(VERSION 3.21)
project(clickables_parser_port LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

find_package(Qt6 REQUIRED COMPONENTS Core Gui Xml)
find_package(QuaZip-Qt6 REQUIRED)

add_library(clickables_parser
    parser.cpp
    parser.hpp
    models.hpp
    database.hpp
)

target_link_libraries(clickables_parser
    PUBLIC
        Qt6::Core
        Qt6::Gui
        Qt6::Xml
        QuaZip::QuaZip
)

target_include_directories(clickables_parser PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})