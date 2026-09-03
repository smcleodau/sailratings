import os
import re

def main():
    directory = 'api/src/irc_data/api/routers'
    for filename in os.listdir(directory):
        if not filename.endswith('.py'):
            continue
        filepath = os.path.join(directory, filename)
        with open(filepath, 'r') as f:
            lines = f.readlines()
        for i, line in enumerate(lines):
            if '@router.post' in line or '@router.put' in line or '@router.delete' in line or '@router.patch' in line:
                if 'admin' in filepath or '/admin' in line or 'admin' in ''.join(lines[:20]):
                    print(f"{filename}:{i+1}: {line.strip()}")

if __name__ == '__main__':
    main()
