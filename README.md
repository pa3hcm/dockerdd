# Dockerdd

Dockerdd is a simple Python script to replicate Docker images between
registries.

## Why Dockerdd

If you have multiple registries and want to keep some images in sync, this
script might be your solution. You simply describe the sync in a yaml file and
run the script.

## Installation

To run this script, you will need:

- Python (including pip)
- Docker
- Python's Docker library

To install the Docker library for Python, run the following command:

``` sh
pip install docker
```

## Upgrading from an older version

The syntax of image entries in the jobs file has changed. If you upgrade
Dockerdd, update your jobs file **before** running the new version, otherwise
the script will fail. Each image entry must now be written as a `name:`
property instead of a plain string:

``` yaml
# Old syntax
imagelists:
  postgres:
    - library/postgres:15.1-alpine

# New syntax
imagelists:
  postgres:
    - name: library/postgres:15.1-alpine
```

## Create the jobs file

This is the structure of a jobs file, it's in yaml format:

``` yaml
registries:
  <registry-name>:
    address: <registry url without the 'http(s)://', or use 'default'>
    folder: <an optional folder name to use at the target registry>
  <registry-name-2>:
    address: ...
  <registry-name-3>:
    address: ...
    folder: ...

imagelists:
  <imagelist-name>:
    - name: image1:tag
    - name: image2:tag
      lts: true
    - name: image3:tag
  <imagelist-name-2>:
    - ...

jobs:
  <job-name>:
    source: <source registry-name>
    target: <target registry-name>
    imagelists:
      - <imagelist-name-1>
      - <imagelist-name-2>
      - ...
  <job-name-2>:
    source: ...
    ...
```

First define the registries you want to use. Define the registry's address,
e.g. `harbor.mydomain.com/dev-images`. You can also use 'default', in that case
Docker will use the default registry as defined by the Docker configuration.
Optionally define a target folder location. If folder is not defined,
the root of the target repository will be used.

Next define imagelists. Each imagelist has a name and contains a list of image
entries. Each entry has the following properties:

- `name`: the image name, including its tag, e.g. `library/postgres:15.1-alpine`.
- `lts`: optional, defaults to `false`. By default an image is skipped when it
  already exists in the target registry. Set `lts: true` for images whose tag
  is updated upstream over time, such as base images like `ubuntu:22.04`. Such
  images are pulled and pushed again on every run, so the target registry
  always gets the latest version of that tag.

Note: image entries must use the `name:` form. Plain strings like
`- image1:tag`, as used by older versions of Dockerdd, are no longer supported.
See [Upgrading from an older version](#upgrading-from-an-older-version).

Finally defines jobs. Each job copies the given imagelists from the given
source registry to the target registry.

You can define multiple jobs and imagelists. Jobs will be performed in the
given order.

## Run the script

``` sh
dockerdd.py <jobs-file>
```

If no jobs file is given, `jobs.yaml` in the current directory is used.

## Digests

For every image that is copied, Dockerdd looks up the manifest digest of the
target image and appends it to `digest.txt` in the current directory, in the
form `<target-image>:<tag>@<digest>`. You can use these digests to pin images
by digest instead of by tag. The file is never truncated, so each run adds new
lines; remove it before a run if you only want the results of that run.

## Authentication

Some registries require authentication, this is not handled by Dockerdd. You
can authenticate once (manually) using `docker login`, the secrets will be
stored in `~/.docker/config.json`.

## Gitlab pipeline

The file `example-gitlab-ci.yml` demonstrates you how you can run this script
in a pipeline.

## License: GPL 3.0

Copyright (C) Ernest Neijenhuis PA3HCM

Dockerdd is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later
version.

Dockerdd is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Dockerdd. If not, see `http://www.gnu.org/licenses/`.

## Project information

* Author: Ernest Neijenhuis
* Code on Github: `https://github.com/pa3hcm/dockerdd`
