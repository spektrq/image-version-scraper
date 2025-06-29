import requests
import re
import argparse

class VersionWithVariant:
    def __init__(self, tag):
        self.original = tag
        if '-' in tag:
            version_str, self.variant = tag.split(tag, 1)
        else:
            version_str, self.variant = tag, None
    
        parts = version_str.split('.')
        if len(parts) != 3:
            raise ValueError(f"Invalid semantic versioning: {version_str}")
        
        self.major, self.minor, self.patch = map(int, parts)

    def __lt__(self, other):
        return (self.major, self.minor, self.patch) < (other.major, other.minor, other.patch)

    def __eq__(self, other):
        return (self.major, self.minor, self.patch) == (other.major, other.minor, other.patch)

    def __repr__(self):
        return f"VersionWithVariant('{self.original}')"
        
    def is_prerelease(self):
        if not self.variant:
            return False
        
        return bool(re.search(r'\b(alpha|beta|rc|pre|next|canary)\b'))
    
    
def get_tags(page_size=100, max_pages=1, base_url='https://hub.docker.com/v2/repositories/library/nginx/tags'):
    tags = []
    print(f"Calling {base_url}")
    for page in range(1, max_pages + 1):
        params = {'page_size': page_size, 'page': page, 'ordering': 'last_updated'}
        response = requests.get(base_url, params=params)
        if response.status_code != 200:
            print(f"Failed to fetch page {page}: {response.status_code}")
            break

        data = response.json()

        # Try to get tags from 'results' names first
        results = data.get('results')
        if results:
            tags.extend(result['name'] for result in results if 'name' in result)
        else:
            # Fallback to 'tags' if available
            fallback_tags = data.get('tags', [])
            if fallback_tags:
                tags.extend(fallback_tags)

        if not data.get('next'):
            break

    return tags


def extract_tag(image_url, default_tag="latest"):
    parts = image_url.split(':')
    # If there are more than 2 parts, this is definitely a port (e.g. localhost:5000/image)
    # Or if the part before the last colon contains a '/' it's probably not a port
    if len(parts) > 2 or ('/' in parts[-2] and '.' not in parts[-2]):
        # Colon belongs to registry port
        return default_tag
    elif ':' in image_url:
        return image_url.rsplit(':', 1)[1]
    else:
        return default_tag


def strip_tag(image_url):
    if ':' not in image_url:
        return image_url
    last_colon_index = image_url.rfind(':')
    slash_after_colon = '/' in image_url[last_colon_index:]
    if slash_after_colon:
        return image_url
    return image_url[:last_colon_index]


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


def get_registry_api_base(image_url):
    registry, repo = parse_image_url(image_url)
    api_base = f"https://{registry}/v2/{repo}/tags/list"
    return api_base

   
def main():
    parser = argparse.ArgumentParser(description="The image-url including image tag")
    parser.add_argument('--image-url', type=str, required=True, help='Docker image URL, e.g., quay.io/kubernetes-ingress-controller/nginx-ingress-controller:0.21.0')

    args = parser.parse_args()
    image_url = args.image_url
    
    current_tag = extract_tag(image_url)
    current_version = VersionWithVariant(current_tag)

    registry_endpoint = get_registry_api_base(image_url)
    tags = get_tags(page_size=50, max_pages=2, base_url=registry_endpoint)

    parsed_tags = []
    for tag in tags:
        try:
            parsed_tags.append(VersionWithVariant(tag))
        except ValueError:
            # print(f"Could not parse image tag version from registry as SemVer spec: {tag} - skipping")
            continue

    results = [tag for tag in parsed_tags if tag > current_version and not tag.is_prerelease()]

    if len(results) > 0:
        print("Newer image versions available:")
        for version in results:
            print(f"{version.original}")
    else:
        print("No newer image versions found.")


if __name__ == "__main__":
    main()
