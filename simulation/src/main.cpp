#include "Simulation.hpp"

int main() {
    Simulation simulation;
    // One illustrative eight-hour production shift, in milliseconds.
    simulation.run(28'800'000);
    return 0;
}
