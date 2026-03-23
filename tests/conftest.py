import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src"))
sys.path.insert(0, os.path.dirname(__file__))

# Ensure coverage output directory exists for pytest-cov (.coveragerc data_file/xml output).
os.makedirs(os.path.join(os.path.dirname(__file__), "../test_data/coverage"), exist_ok=True)
