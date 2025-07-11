import logging
from pathlib import Path
import shutil
import datetime
import cv2
import nibabel as nib
import numpy as np
import pydicom
from pydicom.dataset import FileDataset
from pydicom.uid import generate_uid
from natsort import natsorted

from configs.app_config import AppConfig
from utils.image import apply_ct_window


def convert_nifti_to_dicom(nifti_path: Path, output_folder: Path):
    logging.info(f"Loading NIfTI from {nifti_path} for DICOM conversion.")
    nii_img = nib.load(nifti_path)
    data = nii_img.get_fdata().astype(np.int16)
    affine = nii_img.affine

    patient_id = nifti_path.name.split('.')[0]
    dicom_subfolder = output_folder / patient_id
    dicom_subfolder.mkdir(parents=True, exist_ok=True)

    study_uid, series_uid = generate_uid(), generate_uid()

    for i in range(data.shape[2]):
        slice_data = data[:, :, i]
        pos = affine @ [0, 0, i, 1]

        file_meta = pydicom.Dataset()
        file_meta.MediaStorageSOPClassUID = pydicom.uid.SecondaryCaptureImageStorage
        file_meta.MediaStorageSOPInstanceUID = generate_uid()
        file_meta.ImplementationClassUID = generate_uid(
            prefix=AppConfig.IMPL_CLASS_UID_PREFIX)
        file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian

        ds = FileDataset(dicom_subfolder / f'slice_{i:03d}.dcm', {},
                         file_meta=file_meta, preamble=b"\0" * 128)

        ds.PatientName = f"Patient^{patient_id}"
        ds.PatientID = patient_id
        ds.Modality = "CT"
        ds.StudyInstanceUID, ds.SeriesInstanceUID = study_uid, series_uid
        ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
        ds.SOPClassUID = file_meta.MediaStorageSOPClassUID

        dt = datetime.datetime.now()
        ds.StudyDate, ds.StudyTime = dt.strftime(
            '%Y%m%d'), dt.strftime('%H%M%S')

        ds.Rows, ds.Columns = slice_data.shape
        ds.InstanceNumber = i + 1
        ds.ImagePositionPatient = [str(p) for p in pos[:3]]
        ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        ds.PixelSpacing = [str(abs(affine[0, 0])), str(abs(affine[1, 1]))]
        ds.SliceThickness = str(abs(affine[2, 2]))

        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.BitsAllocated, ds.BitsStored, ds.HighBit = 16, 16, 15
        ds.PixelRepresentation = 1

        ds.PixelData = slice_data.tobytes()
        ds.is_little_endian, ds.is_implicit_VR = True, False
        ds.save_as(ds.filename)

    logging.info(f"DICOM series saved to: {dicom_subfolder}")


def prepare_nifti_slices(nifti_path: Path, output_dir: Path) -> tuple[list[str], int]:
    if not nifti_path.exists():
        logging.error(f"NIfTI file not found: {nifti_path}")
        return [], 0

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    img = nib.load(nifti_path)
    data = img.get_fdata()
    num_slices = data.shape[2]

    slice_files = []
    for i in range(num_slices):
        slice_img_gray = apply_ct_window(data[:, :, i])
        slice_path = output_dir / f"slice_{i:03d}.png"
        cv2.imwrite(str(slice_path), slice_img_gray)
        slice_files.append(str(slice_path))

    return natsorted(slice_files), num_slices


def create_png_masks_from_nifti(nifti_seg_dir: Path, target_organs: list, output_dir: Path):
    patient_folders = natsorted(
        [p for p in nifti_seg_dir.iterdir() if p.is_dir()])

    for patient_dir in patient_folders:
        patient_id = patient_dir.name
        nii_path = patient_dir / f"{patient_id}_trans.nii.gz"

        if not nii_path.is_file():
            logging.warning(f"NIfTI segmentation not found for {patient_id}")
            continue

        logging.info(f"Processing segmentation for patient: {patient_id}")
        nii = nib.load(nii_path)
        label_data = nii.get_fdata()

        if nii.affine[0, 0] > 0:
            label_data = np.flip(label_data, axis=0)
        if nii.affine[1, 1] > 0:
            label_data = np.flip(label_data, axis=1)

        label_data = np.transpose(label_data, (2, 0, 1))

        for organ_name in target_organs:
            try:
                organ_index = AppConfig.ALL_ORGANS.index(organ_name)
            except ValueError:
                logging.warning(
                    f"Organ '{organ_name}' not in master list. Skipping.")
                continue

            patient_organ_dir = output_dir / patient_id / organ_name
            patient_organ_dir.mkdir(parents=True, exist_ok=True)

            for i, slice_label in enumerate(label_data):
                binary_mask = (slice_label == organ_index).astype(
                    np.uint8) * 255
                mask_path = patient_organ_dir / f'slice_{i:03d}_OUT.png'
                cv2.imwrite(str(mask_path), binary_mask)

        logging.info(f"Finished generating masks for patient: {patient_id}")
    logging.info("All PNG masks have been generated.")
