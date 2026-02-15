# Compile Check

Syntax-check all Python files in the project using py_compile.

## Steps

1. Find all Python files and compile-check them:

```bash
".conda/python.exe" -c "
import py_compile, glob, sys
errors = []
files = glob.glob('**/*.py', recursive=True)
files = [f for f in files if '.conda' not in f and '__pycache__' not in f]
for f in sorted(files):
    try:
        py_compile.compile(f, doraise=True)
    except py_compile.PyCompileError as e:
        errors.append(str(e))
print(f'Checked {len(files)} files')
if errors:
    print(f'\n{len(errors)} ERRORS:')
    for e in errors:
        print(e)
    sys.exit(1)
else:
    print('All files OK')
"
```

2. Report results: total files checked, any syntax errors found.
