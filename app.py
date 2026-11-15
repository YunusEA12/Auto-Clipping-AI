"""Compatibility entry point for Streamlit hosting; application code lives in src/."""

import runpy

runpy.run_module("autoclip.dashboard.app", run_name="__main__")
