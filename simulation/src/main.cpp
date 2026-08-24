#include "ConfigLoader.hpp"
#include "Simulation.hpp"
#include <filesystem>
#include <iostream>

int main(int argc, char** argv) {
    try {
        std::filesystem::path factory="config/factory.json", scenario="config/scenarios/mixed_dark_zone.json", defects="config/defects.json", output="output/run_001";
        for(int i=1;i<argc;i++){std::string arg=argv[i]; if(arg=="--factory"&&i+1<argc)factory=argv[++i]; else if(arg=="--scenario"&&i+1<argc)scenario=argv[++i]; else if(arg=="--defects"&&i+1<argc)defects=argv[++i]; else if(arg=="--output"&&i+1<argc)output=argv[++i]; else if(arg=="--help"){std::cout<<"simulation [--factory FILE] [--scenario FILE] [--defects FILE] [--output DIR]\\n";return 0;} else throw std::runtime_error("unknown or incomplete argument: "+arg);}
        Simulation simulation(ConfigLoader::load(factory,scenario,defects),output,scenario.stem().string()); simulation.run();
    } catch(const std::exception& error) { std::cerr<<error.what()<<'\\n'; return 1; }
}
