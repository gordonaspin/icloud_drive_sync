from setuptools import setup, find_packages

with open("requirements.txt") as f:
    required = f.read().splitlines()

setup(
    name="icloudds",
    version="2.0.0",
    url="https://github.com/gordonaspin/icloud_drive_sync",
    description=(
        "icloudds is a command-line tool to synchronize your iCloud Drive to the local filesystem."
    ),
    maintainer="Gordon Aspin",
    maintainer_email="gordon.aspin@gmail.com",
    license="MIT",
    packages=find_packages(),
    install_requires=required,
    classifiers=[
        "Intended Audience :: Developers",
        "Operating System :: OS Independent",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
    ],
    entry_points={"console_scripts": ["icloudds = icloudds.base:main"]},
)
