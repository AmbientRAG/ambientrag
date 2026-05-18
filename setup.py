"""Backward-compatible setup.py for older pip/setuptools (Python 3.9)."""
from setuptools import setup, find_packages

setup(
    packages=find_packages(include=["ambientrag", "ambientrag.*"]),
    package_data={
        "ambientrag": [
            "caps/manifest.json",
            "caps/*/schema.sql",
            "tools/manifest.json",
            "tools/*/schema.sql",
        ],
    },
)
