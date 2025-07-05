import requests
import re
import argparse
import sys
import logging
import time


logger = logging.getLogger(__name__)

def setup_logger(log_level):
    logging.basicConfig(
        level=log_level.upper(),
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    logging.Formatter.converter = time.gmtime  # Log in UTC


class VersionWithVariant:
    def __init__(self, tag):
        self.original = tag
        if '-' in tag:
            version_str, self.variant = tag.split('-', 1)
        else:
            version_str, self.variant = tag, None
    
        parts = version_str.split('.')
        if len(parts) != 3:
            raise ValueError(f"Invalid semantic versioning: {version_str}")
        
        # Drop 'v' from the major version which some image tags have as a suffix
        parts[0] = parts[0].replace('v', '') 
        self.major, self.minor, self.patch = map(int, parts)

    def __eq__(self, other):
        return (self.major, self.minor, self.patch) == (other.major, other.minor, other.patch)
    
    def __lt__(self, other):
        return (self.major, self.minor, self.patch) < (other.major, other.minor, other.patch)

    def __repr__(self):
        return f"VersionWithVariant('{self.original}')"
        
    def is_prerelease(self):
        if not self.variant:
            return False
        
        return bool(re.search(r'\b(alpha|beta|rc|pre|next|canary)\b', self.variant))


def construct_auth_token(url, params=None):
    response = requests.get(url, params=params)
    response.raise_for_status()
    data = response.json()
    token = data.get("token")
    if not token:
        raise Exception(f"Token not found in response: {data}")
    return {"Authorization": f"Bearer {token}"}


def construct_ecr_auth_token():
    return construct_auth_token(url="https://public.ecr.aws/token/")


def construct_dockerhub_auth_token(repo):
    params = {
        "service": "registry.docker.io",
        "scope": f"repository:{repo}:pull",
    }
    return construct_auth_token(url="https://auth.docker.io/token", params=params)
   

def extract_tag(image_url):
    last_colon = image_url.rfind(':')
    last_slash = image_url.rfind('/')

    # If there's colon there's no tag OR if the last colon comes before last slash it's a port and no tag
    if last_colon == -1 or last_colon < last_slash:
        raise Exception(f"No tag found on image url: {image_url}")

    # Otherwise, tag is everything after last colon
    return image_url[last_colon+1:]
    

def get_registry_api_base(registry, repo):
    api_base = f"https://{registry}/v2/{repo}/tags/list"
    return api_base


def get_tags(url, headers, max_pages=3):
    tags = []

    for page in range(1, max_pages + 1):
        params = {'page_size': 100, 'page': page, 'ordering': 'last_updated'}
        
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        
        data = response.json()
        
        # Some registries do not adhere strictly to the response format of the docker registry API spec: https://docker-docs.uclv.cu/registry/spec/api/#tags
        # So we handle both known cases 'results' and 'tags' here
        results = data.get('results', []) 
        if results:
            tags.extend(r['name'] for r in results if 'name' in r)
        else:
            tags.extend(data.get('tags', []))
        
        if not data.get('next'):
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
        action='append',  # Accept one or more image URLs
        help='Docker image URL(s), can be called multiple times e.g., --image-url quay.io/kubernetes-ingress-controller/nginx-ingress-controller:0.21.0 --image-url public.ecr.aws/aws-controllers-k8s/s3-chart:1.0.32'
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
        current_version = VersionWithVariant(current_tag)

        registry, repo = parse_image_url(image_url)
        registry_endpoint = get_registry_api_base(registry, repo)

        if 'public.ecr.aws' in registry:
            headers = construct_ecr_auth_token()
        elif 'docker' in registry:
            headers = construct_dockerhub_auth_token(repo)
        else:
            headers = None

        tags = get_tags(url=registry_endpoint, headers=headers)

        parsed_tags = []
        for tag in tags:
            try:
                parsed_tags.append(VersionWithVariant(tag))
            except ValueError:
                logger.debug(f"Could not parse image tag version from registry as SemVer spec: {tag} - skipping")
                continue

        results = [tag for tag in parsed_tags if tag > current_version and not tag.is_prerelease()]

        if results:
            any_newer_versions_found = True
            logger.info(f"Newer image versions available for {image_url}:")
            for version in results:
                logger.info(f"{version.original}")
        else:
            logger.info(f"No newer image versions found for {image_url}.")

    sys.exit(1 if any_newer_versions_found else 0)


if __name__ == "__main__":
    main()
