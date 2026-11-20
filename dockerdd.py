#!/usr/bin/env python3

"""Dockerdd: replicate Docker images between registries.

Exit codes:
  0  all images were synced
  1  one or more images failed to sync
  2  the jobs file is invalid, or Docker is not available
"""

import subprocess
import sys

import docker
import yaml

DIGEST_FILE = 'digest.txt'
UPGRADE_HINT = "see 'Upgrading from an older version' in the README"


def split_image(image_reference: str):
    """Split an image reference into (name, tag).

    The tag is whatever follows the last ':' after the last '/', so registry
    ports (e.g. 'myreg:5000/foo:1.0') are kept as part of the name. When no
    tag is given, 'latest' is used, just like Docker does. Digest references
    ('foo@sha256:...') are not supported, because a digest cannot be used as a
    tag in the target registry.
    """
    if '@' in image_reference:
        raise ValueError("digest references (name@sha256:...) are not supported, use a tag")
    last_part = image_reference.rsplit('/', 1)[-1]
    if ':' in last_part:
        name, tag = image_reference.rsplit(':', 1)
    else:
        name, tag = image_reference, 'latest'
    if not name or not tag:
        raise ValueError(f"invalid image reference '{image_reference}'")
    return name, tag


def validate_config(config):
    """Check the structure of the jobs file and return a list of errors."""
    if not isinstance(config, dict):
        return ["the file must contain 'registries', 'imagelists' and 'jobs' sections"]

    errors = []
    for section in ('registries', 'imagelists', 'jobs'):
        if not isinstance(config.get(section), dict):
            errors.append(f"missing or invalid '{section}' section")
    if errors:
        return errors

    errors += _validate_registries(config['registries'])
    errors += _validate_imagelists(config['imagelists'])
    errors += _validate_jobs(config['jobs'], config['registries'], config['imagelists'])
    return errors


def _validate_registries(registries):
    errors = []
    for name, registry in registries.items():
        if not isinstance(registry, dict) or not isinstance(registry.get('address'), str):
            errors.append(f"registry '{name}' has no 'address'")
        elif 'folder' in registry and not isinstance(registry['folder'], str):
            errors.append(f"registry '{name}': 'folder' must be a string")
    return errors


def _validate_imagelists(imagelists):
    errors = []
    for name, images in imagelists.items():
        if not isinstance(images, list):
            errors.append(f"imagelist '{name}' must be a list of images")
            continue
        for number, image in enumerate(images, 1):
            if isinstance(image, str):
                errors.append(f"imagelist '{name}', entry {number}: '{image}' uses the old "
                              f"syntax, write it as '- name: {image}' ({UPGRADE_HINT})")
            elif not isinstance(image, dict) or not isinstance(image.get('name'), str):
                errors.append(f"imagelist '{name}', entry {number}: missing 'name'")
            elif not isinstance(image.get('lts', False), bool):
                errors.append(f"imagelist '{name}', entry {number}: 'lts' must be true or false")
    return errors


def _validate_jobs(jobs, registries, imagelists):
    errors = []
    for name, job in jobs.items():
        if not isinstance(job, dict):
            errors.append(f"job '{name}' must have 'source', 'target' and 'imagelists'")
            continue
        for key in ('source', 'target'):
            registry = job.get(key)
            if registry is None:
                errors.append(f"job '{name}' has no '{key}'")
            elif not isinstance(registry, str) or registry not in registries:
                errors.append(f"job '{name}': {key} registry '{registry}' is not defined")
        if 'target_prefix' in job and not isinstance(job['target_prefix'], str):
            errors.append(f"job '{name}': 'target_prefix' must be a string")
        job_imagelists = job.get('imagelists')
        if not isinstance(job_imagelists, list):
            errors.append(f"job '{name}' has no 'imagelists' list")
            continue
        for imagelist in job_imagelists:
            if not isinstance(imagelist, str) or imagelist not in imagelists:
                errors.append(f"job '{name}': imagelist '{imagelist}' is not defined")
    return errors


def load_config(path):
    """Read and validate the jobs file, exit with code 2 if it is invalid."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
    except OSError as e:
        print(f"ERROR: cannot read jobs file: {e}", file=sys.stderr)
        sys.exit(2)
    except yaml.YAMLError as e:
        print(f"ERROR: jobs file '{path}' is not valid YAML: {e}", file=sys.stderr)
        sys.exit(2)

    errors = validate_config(config)
    if errors:
        print(f"ERROR: jobs file '{path}' has errors:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        sys.exit(2)
    return config


def get_target_folder(config, job_name):
    """Return the path to put between the target address and the image name.

    The prefix is set per job with 'target_prefix'. The old 'folder' option on
    the source registry is still accepted as a fallback, but is deprecated.
    """
    job = config['jobs'][job_name]
    target_prefix = job.get('target_prefix')
    source_registry = config['registries'][job['source']]
    if target_prefix is None and 'folder' in source_registry:
        target_prefix = source_registry['folder']
        print(f"  WARNING: 'folder' on registry '{job['source']}' is deprecated, "
              f"use 'target_prefix' on job '{job_name}' instead")
    if target_prefix:
        return '/' + target_prefix.strip('/') + '/'
    return '/'


def image_exists(image_reference):
    """Return True if the image exists in its registry."""
    result = subprocess.run(['docker', 'manifest', 'inspect', image_reference],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            check=False)
    return result.returncode == 0


def remove_local_images(client, *image_references):
    """Remove local images or tags, ignoring the ones that are already gone."""
    for image_reference in image_references:
        try:
            client.images.remove(image_reference)
        except docker.errors.DockerException:
            pass


def push_image(client, image_reference):
    """Push an image and return the digest the registry reports for it.

    The Docker SDK doesn't raise on most push errors, it reports them in the
    progress stream instead, so the stream is checked for errors here.
    """
    digest = None
    for chunk in client.images.push(repository=image_reference, stream=True, decode=True):
        error = chunk.get('error') or chunk.get('errorDetail', {}).get('message')
        if error:
            raise RuntimeError(error)
        aux = chunk.get('aux') or {}
        if 'Digest' in aux:
            digest = aux['Digest']
    return digest


def write_digest(image_reference, digest):
    """Append the digest of a synced image to the digest file."""
    with open(DIGEST_FILE, 'a', encoding='utf-8') as f:
        f.write(f"{image_reference}@{digest}\n")


def sync_image(client, source_full, target_full, refresh):
    """Copy one image from the source to the target registry.

    Returns True if the image is in the target registry afterwards.
    """
    if not refresh and image_exists(target_full):
        print("cached... ok")
        return True

    print("pull... ", end='', flush=True)
    try:
        image = client.images.pull(source_full)
    except docker.errors.DockerException as e:
        print(f"FAIL: {e}")
        return False

    try:
        print("tag... ", end='', flush=True)
        image.tag(target_full)
        print("push... ", end='', flush=True)
        digest = push_image(client, target_full)
    except (docker.errors.DockerException, RuntimeError) as e:
        print(f"FAIL: {e}")
        # Don't leave the pulled image or the target tag on local disk
        print("    cleanup... ", end='', flush=True)
        remove_local_images(client, target_full, source_full)
        print("done")
        return False

    print("untag... ", end='', flush=True)
    remove_local_images(client, target_full)

    print("verify:", end='', flush=True)
    ok = image_exists(target_full)
    print("ok... " if ok else "error... ", end='', flush=True)

    print("cleanup... ", end='', flush=True)
    remove_local_images(client, source_full)
    print("ok" if ok else "FAIL")

    # Only record digests of images that are really in the target registry
    if ok:
        if digest:
            write_digest(target_full, digest)
            print(f"    digest {digest} written to {DIGEST_FILE}")
        else:
            print("    WARNING: the registry reported no digest for this image")
    return ok


def sync_entry(client, image, source_addr, target_addr, target_folder):
    """Sync one imagelist entry, and return True if it succeeded."""
    try:
        image_name, image_tag = split_image(image['name'])
    except ValueError as e:
        print(f"  - {image['name']}: FAIL: {e}")
        return False

    if source_addr == 'default':
        source_full = f"{image_name}:{image_tag}"
    else:
        source_full = f"{source_addr}/{image_name}:{image_tag}"
    target_full = f"{target_addr}{target_folder}{image_name}:{image_tag}"

    print(f"  - {image_name}:{image_tag}: ", end='', flush=True)
    return sync_image(client, source_full, target_full, image.get('lts', False))


def sync_job(client, config, job_name):
    """Sync all imagelists of one job, and return the number of failed images."""
    job = config['jobs'][job_name]
    source = job['source']
    target = job['target']
    source_addr = config['registries'][source]['address']
    target_addr = config['registries'][target]['address']
    target_folder = get_target_folder(config, job_name)

    failures = 0
    for imagelist in job['imagelists']:
        print(f"- Sync imagelist {imagelist} from {source} to {target}")

        for image in config['imagelists'][imagelist]:
            if not sync_entry(client, image, source_addr, target_addr, target_folder):
                failures += 1
    return failures


def main():
    """Run all jobs from the jobs file given on the command line."""
    job_file = sys.argv[1] if len(sys.argv) > 1 else 'jobs.yaml'
    config = load_config(job_file)

    try:
        client = docker.from_env()
    except docker.errors.DockerException as e:
        print(f"ERROR: cannot connect to Docker: {e}", file=sys.stderr)
        sys.exit(2)

    failures = 0
    for job_name in config['jobs']:
        print(f"Job: {job_name}")
        failures += sync_job(client, config, job_name)

    # Exit non-zero if any image failed, so CI pipelines notice failed syncs
    if failures > 0:
        print(f"{failures} image(s) failed to sync")
        sys.exit(1)
    print("All images synced")


if __name__ == '__main__':
    main()
