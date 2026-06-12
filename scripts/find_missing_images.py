import os
import zipfile
import re
from pathlib import Path
from urllib.parse import unquote

def find_missing_images(library_path):
    library = Path(library_path)
    epub_files = list(library.rglob("*.epub"))
    
    print(f"Scanning {len(epub_files)} EPUBs for broken image references...\n")
    
    broken_count = 0
    
    for epub_path in epub_files:
        try:
            with zipfile.ZipFile(epub_path, 'r') as z:
                namelist = set(z.namelist())
                missing_in_this_book = set()
                
                for filename in namelist:
                    if filename.endswith(('.html', '.htm', '.xhtml')):
                        text = z.read(filename).decode('utf-8', 'replace')
                        
                        # Find all img src="..."
                        # Also handle <image href="..."> (SVG) if desired, but sticking to standard <img> for now
                        imgs = re.findall(r'<img[^>]*src=["\']([^"\']+)["\'][^>]*>', text, re.IGNORECASE)
                        
                        for src in imgs:
                            # Strip URL fragments (e.g. image.jpg#1) and decode URL encoded characters
                            src_clean = unquote(src.split('#')[0])
                            
                            # Handle absolute paths within the zip vs relative paths
                            if src_clean.startswith('/'):
                                target_path = src_clean.lstrip('/')
                            else:
                                # Resolve relative path based on the current HTML file's location
                                base_dir = os.path.dirname(filename)
                                if base_dir:
                                    # Normalize the path (handles ../ and ./)
                                    target_path = os.path.normpath(os.path.join(base_dir, src_clean))
                                    # Windows normpath uses '\', but zipfiles use '/'
                                    target_path = target_path.replace('\\', '/')
                                else:
                                    target_path = src_clean
                                    
                            # Check if the target file actually exists in the ZIP archive
                            if target_path not in namelist and not src_clean.startswith(('http://', 'https://', 'data:')):
                                missing_in_this_book.add(src_clean)
                                
                if missing_in_this_book:
                    broken_count += 1
                    rel_path = epub_path.relative_to(library)
                    print(f"[{broken_count}] Broken images in: {rel_path}")
                    for missing in sorted(missing_in_this_book):
                        print(f"    - Missing: {missing}")
                    print()
        except Exception as e:
            pass # Skip corrupted zip files

    print(f"Finished scan. Found {broken_count} books with broken image references.")

if __name__ == "__main__":
    find_missing_images("/home/bdkl/docs/Calibre Library")
