#!/usr/bin/env python3

import docker
import os
import sys
import yaml
import json

# Setup local docker client connection
dockerclient = docker.from_env()

def get_local_image_manifest_digest(image_reference: str):
    try:
        output = os.popen(f"docker manifest inspect -v {image_reference}").read()
        manifest_info = json.loads(output)

        digest = manifest_info['Descriptor']['digest']
        return digest
    except Exception as e:
        print(f"FAIL to get local image manifest digest: {e}")
        return None

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

# Number of images that failed to sync
failures = 0

# Import yaml file
if len(sys.argv) > 1:
    job_file = sys.argv[1]
else:
    job_file = 'jobs.yaml'

from yaml.loader import SafeLoader
with open(job_file, 'r') as f:
    dddata = yaml.load(f, Loader=SafeLoader)

# Process jobs
for job in dddata['jobs']:
    print(f"Job: {job}")

    # Process imagelists for given job
    for imagelist in dddata['jobs'][job]['imagelists']:
        source = dddata['jobs'][job]['source']
        target = dddata['jobs'][job]['target']
        source_addr = dddata['registries'][source]['address']
        target_addr = dddata['registries'][target]['address']

        # Optional path prefix for the images in the target registry, set per
        # job. The old 'folder' option on the source registry is still
        # accepted as a fallback, but is deprecated.
        target_prefix = dddata['jobs'][job].get('target_prefix')
        if target_prefix is None and 'folder' in dddata['registries'][source]:
            target_prefix = dddata['registries'][source]['folder']
            print(f"  WARNING: 'folder' on registry '{source}' is deprecated, "
                  f"use 'target_prefix' on job '{job}' instead")
        if target_prefix:
            target_folder = '/' + target_prefix.strip('/') + '/'
        else:
            target_folder = '/'

        print(f"- Sync imagelist {imagelist} from {source} to {target}")

        # Process images in given imagelist
        for image in dddata['imagelists'][imagelist]:
            image_name = image['name']
            lts = image.get('lts', False)
            result = 'ok'
            try:
                image_name, image_tag = split_image(image_name)
            except ValueError as e:
                print(f"  - {image_name}: FAIL: {e}")
                failures += 1
                continue
            if source_addr == 'default':
                source_full = image_name + ':' + image_tag
            else:
                source_full = source_addr + '/' + image_name + ':' + image_tag
            
            target_full = target_addr + target_folder + image_name + ':' + image_tag

            print(f"  - {image_name}:{image_tag}: ", end='', flush=True)

            # Check if image is already in target registry
            osout = os.system(f"docker manifest inspect {target_full} > /dev/null 2>&1")
            if osout == 0 and not lts:
                print("cached... ok")
            else:
                # Pull image from source registry
                print(f"pull... ", end='', flush=True)
                try:
                    dockerimage = dockerclient.images.pull(source_full)
                except Exception as e:
                    print(f"FAIL: {e}")
                    failures += 1
                    if 'dockerimage' in locals():
                        del dockerimage
                    continue
    
                # Tag new registry
                if 'dockerimage' in locals():
                    print("tag... ", end='', flush=True)
                    try:
                        dockerimage.tag(target_full)
                    except Exception as e:
                        print(f"FAIL: {e}")
                        failures += 1
                        dockerclient.images.remove(source_full)
                        if 'dockerimage' in locals():
                            del dockerimage
                        continue
                    # Get and write the local image digest to a file
                    digest = get_local_image_manifest_digest(target_full)
                    if digest:
                        with open(f"digest.txt", 'a') as file:
                            file.write(f"{target_full}@{digest}\n")
                        print(f" (Digest written {target_full} {digest} digest.txt)")
    
                # Push image to target registry
                if 'dockerimage' in locals():
                    print("push... ", end='', flush=True)
                    try:
                        pushed = dockerclient.images.push(repository=target_full)
                        # Cleanup target image (tag)
                        print("untag... ", end='', flush=True)
                        dockerclient.images.remove(target_full)
                    except Exception as e:
                        print(f"FAIL: {e}")
                        failures += 1
                        # Remove the target tag and the pulled source image,
                        # so failed pushes don't leave images on local disk
                        print("    cleanup... ", end='', flush=True)
                        for leftover in (target_full, source_full):
                            try:
                                dockerclient.images.remove(leftover)
                            except Exception:
                                pass
                        print("done")
                        del dockerimage
                        continue

                    # Check if image is available in target repo
                    print("verify:", end='', flush=True)
                    osout = os.system(f"docker manifest inspect {target_full} >/dev/null 2>/dev/null")
                    if osout != 0:
                        print("error... ", end='', flush=True)
                        result = 'FAIL'
                        failures += 1
                    else:
                        print("ok... ", end='', flush=True)

                    # Cleanup source image
                    print("cleanup... ", end='', flush=True)
                    try:
                        dockerclient.images.remove(source_full)
                    except:
                        pass
                    print(result)

# Report the result and exit non-zero if any image failed, so CI pipelines
# notice failed syncs
if failures > 0:
    print(f"{failures} image(s) failed to sync")
    sys.exit(1)
print("All images synced")

