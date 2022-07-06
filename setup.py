#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""The setup script."""

from setuptools import setup, find_packages

with open('README.rst') as readme_file:
    readme = readme_file.read()

with open('HISTORY.rst') as history_file:
    history = history_file.read()

requirements = [
    'Click>=6.0',
]

setup_requirements = [
    'pytest-runner',
]

test_requirements = [
    'pytest',
]

setup(
    name='satel_integra',
    version='0.3.7',
    description="Communication library and basic testing tool for Satel "
                "Integra alarm system. Communication via tcpip protocol "
                "published by SATEL. ",
    long_description_content_type="text/markdown",
    long_description=readme + '\n\n' + history,    
    author="Krzysztof Machelski",
    author_email='krzysztof.machelski+pypi@gmail.com',
    url='https://github.com/c-soft/satel_integra',
    packages=find_packages(include=['satel_integra']),
    entry_points={
        'console_scripts': [
            'satel_integra=satel_integra.cli:main'
        ]
    },
    include_package_data=True,
    install_requires=requirements,
    license="MIT license",
    zip_safe=False,
    keywords='satel_integra',
    classifiers=[
        'Development Status :: 2 - Pre-Alpha',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Natural Language :: English',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
    ],
    test_suite='tests',
    tests_require=test_requirements,
    setup_requires=setup_requirements,
)
