# DOI Example Templates for Paper Download Skill

This file contains example DOI formats and templates for reference when using the paper download skills.

## Valid DOI Formats

### Standard DOI Format
```
10.1038/s41586-020-2649-2
10.1002/anie.202100001
10.1126/science.abc1234
```

### URL Formats (automatically normalized)
```
https://doi.org/10.1038/s41586-020-2649-2
http://doi.org/10.1002/anie.202100001
https://dx.doi.org/10.1126/science.abc1234
http://dx.doi.org/10.1016/j.cell.2020.01.021
```

## Example DOI Files

### Single DOI File (single_doi.txt)
```
10.1038/s41586-020-2649-2
```

### Multiple DOI File (multiple_dois.txt)
```
10.1038/s41586-020-2649-2
10.1002/anie.202100001
10.1126/science.abc1234
10.1016/j.cell.2020.01.021
https://doi.org/10.1016/j.neuron.2020.01.001
```

### Batch DOI Files (for large downloads)
```
# doi_batch_1.txt
10.1038/s41586-020-2649-2
10.1002/anie.202100001
10.1126/science.abc1234

# doi_batch_2.txt
10.1016/j.cell.2020.01.021
https://doi.org/10.1016/j.neuron.2020.01.001
10.1021/acs.jmedchem.0c12345
```

## Publisher-Specific Examples

### Nature
```
10.1038/s41586-020-2649-2
10.1038/s41591-020-01134-5
10.1038/s41592-020-01026-5
```

### Science/AAAS
```
10.1126/science.abc1234
10.1126/scienceimmunology.abcd123
10.1126/sciadv.abcd1234
```

### Cell Press
```
10.1016/j.cell.2020.01.021
10.1016/j.neuron.2020.01.001
10.1016/j.molcel.2020.01.001
```

### Wiley
```
10.1002/anie.202100001
10.1002/chem.202000123
10.1111/febs.123456
```

### Elsevier
```
10.1016/j.tips.2020.01.001
10.1016/B978-0-12-819456-7.12345-6
10.1016/j.biotechadv.2020.01.001
```

### Springer
```
10.1007/s00401-020-02123-4
10.1038/s41598-020-12345-6
10.1186/s12864-020-12345-6
```

## Usage Examples

### Command Line Usage
```bash
# Single DOI
python .claude/skills/paper-download/scripts/download_by_doi.py --doi 10.1038/s41586-020-2649-2 --verbose

# Multiple DOIs via flags
python .claude/skills/paper-download/scripts/download_multiple_dois.py \
  --doi 10.1038/s41586-020-2649-2 \
  --doi 10.1002/anie.202100001 \
  --doi 10.1126/science.abc1234 \
  --verbose

# From file
python .claude/skills/paper-download/scripts/download_multiple_dois.py \
  --doi-file ./multiple_dois.txt \
  --delay 2.0 \
  --verbose

# Resume from checkpoint
python .claude/skills/paper-download/scripts/download_multiple_dois.py \
  --doi-file ./large_doi_list.txt \
  --resume \
  --delay 1.5 \
  --verbose
```

## File Naming Conventions

### DOI List Files
- `dois.txt` - general DOI list
- `{topic}_dois.txt` - topic-specific (e.g., `catalysis_dois.txt`)
- `{topic}_batch_{number}.txt` - batched files (e.g., `catalysis_batch_1.txt`)
- `{date}_dois.txt` - date-specific (e.g., `2024-01-15_dois.txt`)

### Output Structure
```
downloads/pdfs/
├── 10-1038-s41586-020-2649-2/
│   ├── main.pdf
│   └── supplementary.pdf (if available)
├── 10-1002-anie-202100001/
│   └── main.pdf
└── 10-1126-science-abc1234/
    ├── main.pdf
    └── supplementary.pdf (if available)
```

## Best Practices

1. **One DOI per line** in DOI files
2. **No empty lines** or extra spaces
3. **URL formats are accepted** but will be normalized to DOI format
4. **Batch large downloads** by splitting into smaller files (100-500 DOIs each)
5. **Use appropriate delays** (1.5-2.5 seconds) to respect rate limits
6. **Check the downloads/state/** directory for progress reports

## Error Handling

- Invalid DOI formats will be skipped
- Missing files will be logged but won't stop the batch process
- Check `{filename}_failures.txt` for failed downloads
- Use `--resume` flag to continue from where you left off