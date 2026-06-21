from setuptools import setup, find_packages

setup(
    name="rqfc-dev",
    version="1.1.0",
    packages=find_packages(),
    install_requires=[
        "requests>=2.28.0",
    ],
    python_requires=">=3.9",
    description="Thin client for the RQFC fund trading backend",
    author="RQFC",
)
