import requests
import re
import argparse
import sys
import logging
import time
from urllib.parse import urljoin


logger = logging.getLogger(__name__)

def setup_logger(log_level):
    logging.basicConfig(
        level=log_level.upper(),
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    logging.Formatter.converter = time.gmtime


class ImageVersion:
    def __init__(self, tag):
        self.original = tag
        if '-' in tag:
            version_str, self.variant = tag.split('-', 1)
        else:
            version_str, self.variant = tag, None
    
        parts = version_str.split('.')
        if len(parts) != 3:
            raise ValueError(f"Invalid semantic versioning: {version_str}")
        
        # Drop 'v' from the major version which some image tags have
        parts[0] = parts[0].replace('v', '') 
        self.major, self.minor, self.patch = map(int, parts)

    def __eq__(self, other):
        return (self.major, self.minor, self.patch) == (other.major, other.minor, other.patch)
    
    def __lt__(self, other):
        return (self.major, self.minor, self.patch) < (other.major, other.minor, other.patch)

    def __repr__(self):
        return f"ImageVersion('{self.original}')"
        
    def is_prerelease(self):
        if not self.variant:
            return False
        
        return bool(re.search(r'\b(alpha|beta|rc|pre|next|canary)\b', self.variant))


def get_auth_token(url, params=None):
    response = requests.get(url, params=params)
    response.raise_for_status()
    data = response.json()
    token = data.get("token")
    if not token:
        raise Exception(f"Token not found in response: {data}")
    return token
    

def get_dockerhub_auth_headers(repo):
    params = {
        "service": "registry.docker.io",
        "scope": f"repository:{repo}:pull",
    }
    token = get_auth_token(url="https://auth.docker.io/token", params=params)
    return {"Authorization": f"Bearer {token}"}


def get_ecr_auth_headers():
    token = get_auth_token(url="https://public.ecr.aws/token")
    return {"Authorization": f"Bearer {token}"}


def get_ghcr_auth_headers(token):
    if not token:
        raise ValueError("GitHub token is required to process images from GHCR.")
    return {"Authorization": f"Bearer {token}"}
   

def extract_tag(image_url):
    last_colon = image_url.rfind(':')
    last_slash = image_url.rfind('/')

    # if there's no colon then there's no tag OR
    # if the last colon comes before the last slash it's only a port and no tag
    if last_colon == -1 or last_colon < last_slash:
        raise ValueError(f"No tag found on image url: {image_url}")

    # Otherwise, tag is everything after last colon
    return image_url[last_colon+1:]


def get_auth_headers(registry: str, repo: str, github_token: str):
    if 'public.ecr.aws' in registry:
        return get_ecr_auth_headers()
    elif 'docker' in registry:
        return get_dockerhub_auth_headers(repo)
    elif 'ghcr' in registry:
        return get_ghcr_auth_headers(github_token)
    else:
        return None
    

def get_tags(url, headers):
    tags = []
    logger.debug(f"Calling {url}")
    params = {'page_size': 100,'ordering': 'last_updated'}

    for _ in range(100):
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()

        data = response.json()
        logger.debug(f"Response data: {data}")

        # See https://docker-docs.uclv.cu/registry/spec/api/#listing-image-tags
        if data.get('tags'):
            tags.extend(data.get('tags')) 
        
        next_link = response.links.get('next', {}).get('url')

        if next_link:
            url = urljoin(url, next_link)
        else:
            break

    return tags


def parse_image_url(image_url):
    default_registry = "registry-1.docker.io"

    image = strip_tag(image_url) 

    if '/' not in image:
        registry = default_registry
        repo = f"library/{image}"
    else:
        parts = image.split('/')
        if '.' in parts[0] or ':' in parts[0]:
            registry = parts[0]
            repo = '/'.join(parts[1:])
        else:
            registry = default_registry
            repo = image

    return registry, repo


def strip_tag(image_url):
    if ':' not in image_url:
        return image_url
    last_colon_index = image_url.rfind(':')
    slash_after_colon = '/' in image_url[last_colon_index:]
    if slash_after_colon:
        return image_url
    return image_url[:last_colon_index]

   
def main():
    parser = argparse.ArgumentParser(description="Checks for new image versions and fails if any new ones are found")
    parser.add_argument(
        '--image-url',
        type=str,
        required=True,
        nargs='+',
        help='Docker image URL(s) including version tag, separated by a space e.g., --image-url quay.io/kubernetes-ingress-controller/nginx-ingress-controller:0.21.0 public.ecr.aws/aws-controllers-k8s/s3-chart:1.0.32'
    )
    parser.add_argument(
        '--github-token',
        default='',
        help='Valid GitHub token to use if the image is stored in GHCR'
    )
    parser.add_argument(
        '--log-level',
        default='INFO',
        help='Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL) e.g., --log-level DEBUG'
    )

    args = parser.parse_args()
    setup_logger(log_level=args.log_level)

    any_newer_versions_found = False
    
    for image_url in args.image_url:
        logger.info(f"Checking image: {image_url}")

        current_tag = extract_tag(image_url)
        current_version = ImageVersion(current_tag)

        registry, repo = parse_image_url(image_url)

        # https://docker-docs.uclv.cu/registry/spec/api/#tags
        registry_endpoint = f"https://{registry}/v2/{repo}/tags/list"
        headers = get_auth_headers(registry, repo, args.github_token)

        tags = get_tags(url=registry_endpoint, headers=headers)

        results = []
        for tag in tags:
            try:
                version = ImageVersion(tag)
                if version > current_version and not version.is_prerelease():
                    results.append(version)
            except ValueError:
                logger.debug(f"Could not parse image tag version from registry as SemVer spec: {tag} - skipping")

        if results:
            any_newer_versions_found = True
            results.sort()
            logger.info(f"Newer image versions available for {image_url}:")

            for version in results:
                logger.info(f"{version.original}")
        else:
            logger.info(f"No newer image versions found for {image_url}.")

    sys.exit(1 if any_newer_versions_found else 0)


if __name__ == "__main__":
    main()
