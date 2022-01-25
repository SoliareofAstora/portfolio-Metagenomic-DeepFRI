import argparse
import gzip
import logging
import multiprocessing
import pathlib
import traceback

from itertools import repeat
import numpy as np

from CONFIG.FOLDER_STRUCTURE import TARGET_MMSEQS_DB_NAME, ATOMS, SEQUENCES, STRUCTURE_FILES_PATH, \
    DEFAULT_NAME, SEQ_ATOMS_DATASET_PATH, MMSEQS_DATABASES_PATH, MERGED_SEQUENCES
from CONFIG.RUNTIME_PARAMETERS import CPU_COUNT, MAX_TARGET_CHAIN_LENGTH
from utils.bio_utils import PROTEIN_LETTERS

from structure_files_parsers.parse_pdb import parse_pdb
from structure_files_parsers.parse_mmcif import parse_mmcif

from CPP_lib.libAtomDistanceIO import save_atoms
from CPP_lib.libAtomDistanceIO import initialize as initialize_CPP_LIB
from utils.mmseqs_utils import mmseqs_createdb
from utils.mmseqs_utils import mmseqs_createindex
from utils.utils import create_unix_timestamp_folder, merge_files_binary

STRUCTURE_FILES_PARSERS = {
    '.pdb': parse_pdb,
    '.pdb.gz': parse_pdb,
    '.cif': parse_mmcif,
    '.cif.gz': parse_mmcif,
    '.ent': parse_pdb,
    '.ent.gz': parse_pdb
}


def parse_args():
    parser = argparse.ArgumentParser(description="Read structure files from folders --input to extract sequence and atom positions. "
                                                 "Create and index new --output MMSEQS2 database")

    parser.add_argument("-i", "--input", nargs='+', required=False, default=[STRUCTURE_FILES_PATH], help="Paths to folders containing structure files")
    parser.add_argument("-n", "--name", required=False, default=DEFAULT_NAME, help="Name of the database")
    parser.add_argument("--overwrite", action="store_true", help="Flag to override existing sequences and atom positions")
    return parser.parse_args()


def parse_structure_file(structure_file, save_path):
    protein_id = structure_file.name

    # extract sequence and atom positions from structure file
    try:
        for pattern in STRUCTURE_FILES_PARSERS.keys():
            if protein_id.endswith(pattern):
                if protein_id.endswith('.gz'):
                    with gzip.open(structure_file, 'rt') as f:
                        atom_amino_group, positions, groups = STRUCTURE_FILES_PARSERS[pattern](f)
                else:
                    with open(structure_file, 'r') as f:
                        atom_amino_group, positions, groups = STRUCTURE_FILES_PARSERS[pattern](f)
                protein_id = structure_file.name.replace(pattern, '')
                break

    except Exception:
        print("EXCEPTION WHILE READING FILE ", str(structure_file))
        logging.error(traceback.format_exc())
        return "file reading exceptions"

    # process and store sequence and atom positions in SEQ_ATOMS_DATASET_PATH / output_name
    sequence_path = save_path / SEQUENCES / (protein_id + ".faa")
    atoms_path = save_path / ATOMS / (protein_id + ".bin")
    try:
        _, groups = np.unique(groups, return_index=True)
        groups.sort()

        if len(groups) < 9:
            # print("Files containing protein chains shorter than 9 amino acids might be corrupted or contain DNA ", file)
            return "sequences too short, probably DNA or corrupted"

        truncated = False
        if len(groups) > MAX_TARGET_CHAIN_LENGTH:
            # print(f"Protein chains with more than {MAX_TARGET_CHAIN_LENGTH} are truncated. {str(structure_file)}\n"
            #       f"Change CONFIG/RUNTIME_PARAMETERS.py :MAX_TARGET_CHAIN_LENGTH and rerun this script with --overwrite to process this file again")
            truncated = True
            group_indexes = groups[:MAX_TARGET_CHAIN_LENGTH]
            group_indexes = np.append(group_indexes, groups[MAX_TARGET_CHAIN_LENGTH]).astype(np.int32)
        else:
            group_indexes = np.append(groups, positions.shape[0]).astype(np.int32)

        sequence = ''.join([PROTEIN_LETTERS[atom_amino_group[i]] for i in group_indexes[:-1]])
        with open(sequence_path, "w") as f:
            f.write(f">{protein_id}\n{sequence}\n")
        save_atoms(positions, group_indexes, str(atoms_path))

        if truncated:
            return f"succeed, but sequences and contact maps got truncated to {MAX_TARGET_CHAIN_LENGTH}"
        else:
            return "SUCCEED"

    except Exception:
        print("EXCEPTION DURING FILE PROCESSING ", str(structure_file))
        logging.error(traceback.format_exc())
        sequence_path.unlink(missing_ok=True)
        atoms_path.unlink(missing_ok=True)
        return "file processing exceptions"


def main(input_paths, output_name, overwrite):
    seq_atoms_path = SEQ_ATOMS_DATASET_PATH / output_name
    seq_atoms_path.mkdir(exist_ok=True, parents=True)
    print("Sequences and Atoms positions will be stored in: ", seq_atoms_path)
    (seq_atoms_path / SEQUENCES).mkdir(exist_ok=True)
    (seq_atoms_path / ATOMS).mkdir(exist_ok=True)

    print("Searching for structure files in: \n\t", [str(x) for x in input_paths])
    structure_files = dict()
    for input_path in input_paths:
        print(input_path)
        for pattern in STRUCTURE_FILES_PARSERS.keys():
            pattern_structure_files = list(input_path.glob("**/*" + pattern))
            pattern_structure_ids = [x.name[:-len(pattern)] for x in pattern_structure_files]
            if len(pattern_structure_files) == 0:
                continue
            print("\tFound", len(pattern_structure_files), pattern, "files in", input_path)
            structure_files.update(zip(pattern_structure_ids, pattern_structure_files))

    if len(structure_files) == 0:
        print("No structure files found")
        return

    # search for already processed protein_ids to skip them
    if not overwrite:
        existing_structures = set([x.name[:-4] for x in (seq_atoms_path / ATOMS).glob("**/*.bin")])
        print("\nFound ", len(existing_structures), " already processed structures")
        duplicated_ids_counter = 0
        for structure_id in list(structure_files.keys()):
            if structure_id in existing_structures:
                structure_files.pop(structure_id)
                duplicated_ids_counter += 1
        print("Found ", duplicated_ids_counter, " duplicated IDs")

    print("\nProcessing", len(structure_files), "files")
    initialize_CPP_LIB()
    with multiprocessing.Pool(processes=CPU_COUNT) as p:
        logs = p.starmap(parse_structure_file, zip(structure_files.values(), repeat(seq_atoms_path.absolute())))

    unique_logs, logs_counts = np.unique(logs, return_counts=True)
    for i in range(len(unique_logs)):
        print(f"\t{logs_counts[i]} {unique_logs[i]}")

    sequence_files = list((seq_atoms_path / SEQUENCES).glob("**/*.faa"))
    print("\nMerging " + str(len(sequence_files)) + " sequence files for mmseqs2")
    merge_files_binary(sequence_files, seq_atoms_path / MERGED_SEQUENCES)

    mmseqs2_path = create_unix_timestamp_folder(MMSEQS_DATABASES_PATH / output_name)
    print("Creating new target mmseqs2 database " + str(mmseqs2_path))
    mmseqs_createdb(seq_atoms_path / MERGED_SEQUENCES, mmseqs2_path / TARGET_MMSEQS_DB_NAME)
    print("Indexing new target mmseqs2 database " + str(mmseqs2_path))
    mmseqs_createindex(mmseqs2_path / TARGET_MMSEQS_DB_NAME)


if __name__ == '__main__':
    args = parse_args()

    input_paths = [pathlib.Path(x) for x in args.input]
    output_name = pathlib.Path(args.name)
    overwrite = args.overwrite

    main(input_paths, output_name, overwrite)