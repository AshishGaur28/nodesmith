#pragma once
#include <cmath>
#include <cstdio>
#include <cstdlib>
#define CHECK(cond) do { if (!(cond)) { std::fprintf(stderr, "CHECK failed at %s:%d: %s\n", __FILE__, __LINE__, #cond); std::exit(1); } } while (0)
#define CHECK_NEAR(a, b) CHECK(std::fabs((a) - (b)) < 1e-9)
