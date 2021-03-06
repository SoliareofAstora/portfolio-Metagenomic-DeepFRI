import json
import pandas as pd
import pathlib
import pathos

from Bio import pairwise2

from CONFIG.FOLDER_STRUCTURE import ALIGNMENTS
from CONFIG.RUNTIME_PARAMETERS import CPU_COUNT
from utils.seq_file_loader import SeqFileLoader


# alignment sequence identity return value between 0 and 1
def alignment_sequences_identity(alignment):
    matches = [alignment.seqA[i] == alignment.seqB[i] for i in range(len(alignment.seqA))]
    seq_id = sum(matches) / len(alignment.seqA)
    return seq_id


def align(query_seq, target_seq, match, missmatch, gap_open, gap_continuation):
    return pairwise2.align.globalms(query_seq, target_seq,
                                    match, missmatch, gap_open, gap_continuation,
                                    one_alignment_only=True)[0]


def search_alignments(query_seqs: dict, mmseqs_search_output: pd.DataFrame, target_seqs: SeqFileLoader,
                      job_path: pathlib.Path, job_config):
    # format of output JSON file:
    # alignments = dict[query_id]
    #     "target_id": target_id,
    #     "sequence_identity" : alignment_sequence_identity(alignment)
    #     "alignment": alignment = biopython.alignment
    #         0. seqA = query_sequence
    #         1. seqB = target_sequence
    #         2. score = biopython alignment score
    #         3. start and end of alignment

    alignments_json_file = job_path / ALIGNMENTS
    if alignments_json_file.exists():
        return json.load(open(alignments_json_file, "r"))

    print(f"MMseqs search output is {len(mmseqs_search_output)} long.")
    query_seqs_keys = list(query_seqs.keys())
    filtered_mmseqs_search = mmseqs_search_output[mmseqs_search_output['bit_score'] > job_config["MMSEQS_MIN_BIT_SCORE"]]
    filtered_mmseqs_search = filtered_mmseqs_search[filtered_mmseqs_search['e_value'] < job_config["MMSEQS_MAX_EVAL"]]
    filtered_mmseqs_search = filtered_mmseqs_search[filtered_mmseqs_search['identity'] > job_config["MMSEQS_MIN_IDENTITY"]]
    filtered_mmseqs_search = filtered_mmseqs_search[filtered_mmseqs_search['query'].isin(query_seqs_keys)]
    print(f"Filtered {len(mmseqs_search_output) - len(filtered_mmseqs_search)} mmseqs matches. "
          f"Total alignments to check {len(filtered_mmseqs_search)}")

    queries = list(map(lambda x: query_seqs[x], filtered_mmseqs_search["query"]))
    targets = list(map(lambda x: target_seqs[x], filtered_mmseqs_search["target"]))

    # Couldn't find more elegant solution on how to use repeating values for pathos.multiprocessing
    # Standard multiprocessing.Pool is out of reach due to problematic pairwise2.align.globalms behaviour.
    # todo make some runtime tests, maybe chunkified sequences will perform better
    match = [job_config["PAIRWISE_ALIGNMENT_MATCH"]] * len(queries)
    missmatch = [job_config["PAIRWISE_ALIGNMENT_MISSMATCH"]] * len(queries)
    gap_open = [job_config["PAIRWISE_ALIGNMENT_GAP_OPEN"]] * len(queries)
    gap_continuation = [job_config["PAIRWISE_ALIGNMENT_GAP_CONTINUATION"]] * len(queries)

    with pathos.multiprocessing.ProcessingPool(processes=CPU_COUNT) as p:
        all_alignments = p.map(align, queries, targets, match, missmatch, gap_open, gap_continuation)

    alignments = dict()
    for i in range(len(all_alignments)):
        alignment = all_alignments[i]
        # filter out bad alignments based on alignment sequences identity
        sequence_identity = alignment_sequences_identity(alignment)
        if sequence_identity > job_config["ALIGNMENT_MIN_SEQUENCE_IDENTITY"]:
            query_id = filtered_mmseqs_search["query"].iloc[i]
            target_id = filtered_mmseqs_search["target"].iloc[i]
            if query_id not in alignments.keys():
                alignments[query_id] = {"target_id": target_id, "alignment": alignment, "sequence_identity": sequence_identity}
            # select best alignment based on pairwise2.align.globalms.score
            elif alignment.score > alignments[query_id]["alignment"].score:
                alignments[query_id] = {"target_id": target_id, "alignment": alignment, "sequence_identity": sequence_identity}

    json.dump(alignments, open(alignments_json_file, "w"), indent=4, sort_keys=True)
    return alignments
