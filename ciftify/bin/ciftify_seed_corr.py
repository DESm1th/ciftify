#!/usr/bin/env python
"""
Produces a correlation map of the mean time series within the seed with
every voxel in the functional file.

Usage:
    ciftify_seed_corr [options] <func> <seed>

Arguments:
    <func>          functional data (nifti or cifti)
    <seed>          seed mask (nifti, cifti or gifti)

Options:
    --outputname STR   Specify the output filename
    --output-ts        Also output write the from the seed to text
    --roi-label INT    Specify the numeric label of the ROI you want a seedmap for
    --hemi HEMI        If the seed is a gifti file, specify the hemisphere (R or L) here
    --mask FILE        brainmask
    --weighted         compute weighted average timeseries from the seed map
    --use-TRs FILE     Only use the TRs listed in the file provided (TR's in file starts with 1)
    --debug            Debug logging
    -h, --help         Prints this message

DETAILS:
The default output filename is created from the <func> and <seed> filenames,
(i.e. func.dscalar.nii + seed.dscalar.nii --> func_seed.dscalar.nii)
and written to same folder as the <func> input. Use the --outputname
argument to specify a different outputname. The output datatype matches the <func>
input.

The mean timeseries is calculated using ciftify_meants, --roi-label, --hemi,
--mask, and --weighted arguments are passed to it. See ciftify_meants --help for
more info on their usage. The timeseries output (*_meants.csv) of this step can be
saved to disk using the --output-ts option.

If a mask is provided with the (--mask) option. (Such as a brainmask) it will be
applied to both the seed and functional file.

The '--use-TRs' argument allows you to calcuate the correlation maps from specific
timepoints (TRs) in the timeseries. This option can be used to exclude outlier
timepoints or to limit the calculation to a subsample of the timecourse
(i.e. only the beggining or end). It expects a text file containing the integer numbers
TRs to keep (where the first TR=1).

Written by Erin W Dickie
"""
import os
import sys
import subprocess
import tempfile
import shutil
import logging
import logging.config

import numpy as np
import scipy as sp
import nibabel as nib
from docopt import docopt

import ciftify
from ciftify.utils import run

# Read logging.conf
config_path = os.path.join(os.path.dirname(__file__), "logging.conf")
logging.config.fileConfig(config_path, disable_existing_loggers=False)
logger = logging.getLogger(os.path.basename(__file__))

def main():
    global DRYRUN

    arguments = docopt(__doc__)
    func   = arguments['<func>']
    seed   = arguments['<seed>']
    mask   = arguments['--mask']
    roi_label = arguments['--roi-label']
    outputname = arguments['--outputname']
    weighted = arguments['--weighted']
    TR_file = arguments['--use-TRs']
    output_ts = arguments['--output-ts']
    hemi = arguments['--hemi']
    debug = arguments['--debug']

    if debug:
        logger.setLevel(logging.DEBUG)
        logging.getLogger('ciftify').setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.WARNING)
        logging.getLogger('ciftify').setLevel(logging.WARNING)

    ## make the tempdir
    tempdir = tempfile.mkdtemp()

    func_type, funcbase = ciftify.io.determine_filetype(func)
    seed_type, seedbase = ciftify.io.determine_filetype(seed)
    if mask:
        mask_type, maskbase = ciftify.io.determine_filetype(mask)
    else: mask_type = None

    logger.debug('func_type: {}, funcbase: {}'.format(func_type, funcbase))
    logger.debug('seed_type:{}, seedbase: {}'.format(seed_type, seedbase))

    ## determine outbase if it has not been specified
    if not outputname:
        outputdir = os.path.dirname(func)
        outbase = '{}_{}'.format(funcbase, seedbase)
        outputname = os.path.join(outputdir, outbase)
    else:
        outbase = outputname.replace('nii.gz','').replace('.dscalar.nii','')

    logger.debug(outbase)

    ## run ciftify-meants to get the ts file
    ts_tmpfile = os.path.join(tempdir, '{}_meants.csv'.format(outbase))
    meants_cmd = ['ciftify_meants']
    if mask_type: meants_cmd.extend(['--mask', mask])
    if weighted: meants_cmd.append('--weighted')
    if roi_label: meants_cmd.extend(['--roi-label',roi_label])
    if hemi: meants_cmd.extend(['--hemi',hemi])
    meants_cmd.extend(['--outputcsv', ts_tmpfile, func, seed])
    run(meants_cmd)

    # load the file we just made
    seed_ts = np.loadtxt(ts_tmpfile, delimiter=',')

    ## convert to nifti
    if func_type == "cifti":
        func_fnifti = os.path.join(tempdir,'func.nii.gz')
        run(['wb_command','-cifti-convert','-to-nifti',func, func_fnifti])
        func_data, outA, header, dims = ciftify.io.load_nifti(func_fnifti)

    # import template, store the output paramaters
    if func_type == "nifti":
        func_data, outA, header, dims = ciftify.io.load_nifti(func)

    if mask_type == "cifti":
        mask_fnifti = os.path.join(tempdir,'mask.nii.gz')
        run(['wb_command','-cifti-convert','-to-nifti', mask, mask_fnifti])
        mask_data, _, _, _ = ciftify.io.load_nifti(mask_fnifti)

    if mask_type == "nifti":
        mask_data, _, _, _ = ciftify.io.load_nifti(mask)

    # decide which TRs go into the correlation
    if TR_file:
        TR_file = np.loadtxt(TR_file, int)
        TRs = TR_file - 1 # shift TR-list to be zero-indexed
    else:
        TRs = np.arange(dims[3])

    # get mean seed timeseries
    ## even if no mask given, mask out all zero elements..
    std_array = np.std(func_data, axis=1)
    m_array = np.mean(func_data, axis=1)
    std_nonzero = np.where(std_array > 0)[0]
    m_nonzero = np.where(m_array != 0)[0]
    idx_mask = np.intersect1d(std_nonzero, m_nonzero)
    if mask:
        idx_of_mask = np.where(mask_data > 0)[0]
        idx_mask = np.intersect1d(idx_mask, idx_of_mask)

    # create output array
    out = np.zeros([dims[0]*dims[1]*dims[2], 1])

    # look through each time series, calculating r
    for i in np.arange(len(idx_mask)):
        out[idx_mask[i]] = np.corrcoef(seed_ts[TRs], func_data[idx_mask[i], TRs])[0][1]

    # create the 3D volume and export
    out = out.reshape([dims[0], dims[1], dims[2], 1])
    out = nib.nifti1.Nifti1Image(out, outA)

    # write out nifti
    if func_type == "nifti":
        if outputname.endswith(".nii.gz"):
            out.to_filename(outputname)
        else:
            out.to_filename('{}.nii.gz'.format(outputname))

    if func_type == "cifti":
        out.to_filename(os.path.join(tempdir,'out.nii.gz'))
        run(['wb_command', '-cifti-reduce', func, 'MIN', os.path.join(tempdir, 'template.dscalar.nii')])

        ## convert back
        if not outputname.endswith('.dscalar.nii'):
            outputname = '{}.dscalar.nii'.format(outputname)
        run(['wb_command','-cifti-convert','-from-nifti',
            os.path.join(tempdir,'out.nii.gz'),
            os.path.join(tempdir, 'template.dscalar.nii'),
            outputname])

    # write out the ts if asked
    if output_ts:
        run(['cp', ts_tmpfile, '{}_meants.csv'.format(outbase)])

    ## remove the tempdirectory
    shutil.rmtree(tempdir)

if __name__ == '__main__':
    main()