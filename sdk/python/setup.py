from setuptools import setup, find_packages

setup(
    name="consent360",
    version="1.0.0",
    description="Consent360 Integration SDK — manage customer consent via the Consent360 API",
    author="Consent360",
    license="MIT",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[],
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Software Development :: Libraries",
    ],
)
