from setuptools import find_packages
from setuptools import setup

from utils.utils import run_command

setup(
    name="metagenomic-deepFRI",
    version="0.1",
    description="Pipeline for searching and aligning contact maps for proteins",
    author="Piotr Kucharski",
    author_email="piotr1kucharski@gmail.com",
    url="https://github.com/bioinf-mcb/metagenomic-deepFRI",
    download_url="https://github.com/bioinf-mcb/metagenomic-deepFRI",
    license="GNU GPLv3",
    install_requires=[
        "biopython==1.79",
        "numpy==1.21.5",
        "pandas==1.3.5",
        "pathos==0.2.8",
        "scikit-learn==1.0.2",
        "tensorflow==2.7.0",
    ],
    packages=find_packages(),
)

print("Please install additional apt-get packages")
print("apt-get install libboost-numpy1.71-dev libboost-python1.71-dev")