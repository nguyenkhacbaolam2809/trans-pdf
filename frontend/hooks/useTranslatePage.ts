"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useJobRunner } from "./useJobRunner";
import { useElapsed } from "./useElapsed";
import {
  createJob,
  deleteJob,
  downloadJobBlob,
  fetchJobQa,
  previewPageBlob,
  submitJobEdits,
  syncTranslateBlob,
} from "../lib/api";
import type { Mode, QaBlock, QaSummary } from "../types";

/** State + actions của trang dịch. Trang chỉ lo render. */
export function useTranslatePage() {
  const [mode, setMode] = useState<Mode>("job");
  const [file, setFile] = useState<File | null>(null);
  const [targetLang, setTargetLang] = useState("Vietnamese");
  const [previewPage, setPreviewPage] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null);
  const [downloadName, setDownloadName] = useState("");
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewMeta, setPreviewMeta] = useState("");
  const [qaSummary, setQaSummary] = useState<QaSummary | null>(null);
  const [qaBlocks, setQaBlocks] = useState<QaBlock[]>([]);
  const [editBusy, setEditBusy] = useState(false);
  const [editNote, setEditNote] = useState("");

  const [elapsed] = useElapsed(busy);
  const runner = useJobRunner({
    onDone: useCallback(async (state) => {
      setBusy(false);
      const id = state.job_id;
      if (!id) return;
      try {
        const { blob, fileName } = await downloadJobBlob(id);
        setDownloadUrl((old) => {
          if (old) URL.revokeObjectURL(old);
          return URL.createObjectURL(blob);
        });
        setDownloadName(fileName || state.output_filename || "");
        const qa = await fetchJobQa(id);
        setQaSummary(qa.summary);
        setQaBlocks(qa.blocks);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Không tải được kết quả.");
      }
    }, []),
    onError: useCallback((msg: string) => {
      setBusy(false);
      setError(msg);
    }, []),
  });

  // Ref ổn định cho cleanup unmount (xem effect bên dưới).
  const stopRef = useRef(runner.stop);
  stopRef.current = runner.stop;
  const urlsRef = useRef({ downloadUrl, previewUrl });
  urlsRef.current = { downloadUrl, previewUrl };

  useEffect(() => {
    // Đợt A: cleanup này chỉ được chạy khi unmount. Trước đây effect phụ thuộc
    // object `runner` (đổi identity mỗi render) nên mỗi tin tiến trình về là
    // re-render, cleanup gọi runner.stop() đóng WebSocket và xóa poll — job
    // kẹt mãi không xong. Giờ chỉ bám vào `stop` ổn định, URL đọc qua ref.
    return () => {
      stopRef.current();
      const { downloadUrl: d, previewUrl: p } = urlsRef.current;
      if (d) URL.revokeObjectURL(d);
      if (p) URL.revokeObjectURL(p);
    };
  }, []);

  const resetTransient = useCallback(() => {
    setError("");
    setEditNote("");
    setQaSummary(null);
    setQaBlocks([]);
    setPreviewMeta("");
  }, []);

  const pickFile = useCallback(
    (f: File) => {
      runner.stop();
      setFile(f);
      setJobId(null);
      setDownloadUrl((old) => {
        if (old) URL.revokeObjectURL(old);
        return null;
      });
      setPreviewUrl((old) => {
        if (old) URL.revokeObjectURL(old);
        return null;
      });
      resetTransient();
    },
    [runner, resetTransient]
  );

  const resetAll = useCallback(() => {
    runner.stop();
    setBusy(false);
    setFile(null);
    setJobId(null);
    setDownloadUrl((old) => {
      if (old) URL.revokeObjectURL(old);
      return null;
    });
    setPreviewUrl((old) => {
      if (old) URL.revokeObjectURL(old);
      return null;
    });
    resetTransient();
  }, [runner, resetTransient]);

  const cancelJob = useCallback(async () => {
    runner.stop();
    if (jobId) await deleteJob(jobId);
    setBusy(false);
    runner.setStep("Đã hủy");
    runner.setDetail("");
  }, [runner, jobId]);

  const submitJob = useCallback(async () => {
    if (!file || busy) return;
    setBusy(true);
    setError("");
    try {
      const { job_id } = await createJob(file, targetLang);
      setJobId(job_id);
      runner.watch(job_id);
    } catch (e) {
      setBusy(false);
      setError(e instanceof Error ? e.message : "Không gửi được job.");
    }
  }, [file, busy, targetLang, runner]);

  const syncTranslate = useCallback(async () => {
    if (!file || busy) return;
    setBusy(true);
    setError("");
    runner.setProgress(20);
    runner.setStep("Đang dịch");
    try {
      const { blob, fileName } = await syncTranslateBlob(file, targetLang);
      setDownloadUrl((old) => {
        if (old) URL.revokeObjectURL(old);
        return URL.createObjectURL(blob);
      });
      setDownloadName(fileName);
      runner.setProgress(100);
      runner.setStep("Xong");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Dịch nhanh thất bại.");
    } finally {
      setBusy(false);
    }
  }, [file, busy, targetLang, runner]);

  const runPreview = useCallback(async () => {
    if (!file || busy) return;
    setBusy(true);
    setError("");
    try {
      const { blob, meta } = await previewPageBlob(
        file,
        targetLang,
        previewPage
      );
      setPreviewUrl((old) => {
        if (old) URL.revokeObjectURL(old);
        return URL.createObjectURL(blob);
      });
      setPreviewMeta(meta);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Xem trước thất bại.");
    } finally {
      setBusy(false);
    }
  }, [file, busy, targetLang, previewPage]);

  const saveEdits = useCallback(
    async (edits: Record<string, string>) => {
      if (!jobId || editBusy) return;
      if (Object.keys(edits).length === 0) {
        setEditNote("Bạn chưa sửa đoạn nào.");
        return;
      }
      setEditBusy(true);
      try {
        const res = await submitJobEdits(jobId, edits);
        setEditNote(`Đã vẽ lại với ${res.edited_blocks} đoạn sửa.`);
        setQaSummary(res.qa_summary || qaSummary);
        const { blob, fileName } = await downloadJobBlob(jobId);
        setDownloadUrl((old) => {
          if (old) URL.revokeObjectURL(old);
          return URL.createObjectURL(blob);
        });
        setDownloadName(fileName);
        const qa = await fetchJobQa(jobId);
        setQaSummary(qa.summary);
        setQaBlocks(qa.blocks);
      } catch (e) {
        setEditNote(e instanceof Error ? e.message : "Lưu thất bại.");
      } finally {
        setEditBusy(false);
      }
    },
    [jobId, editBusy, qaSummary]
  );

  return {
    mode,
    setMode,
    file,
    targetLang,
    setTargetLang,
    previewPage,
    setPreviewPage,
    busy,
    error,
    setError,
    jobId,
    downloadUrl,
    downloadName,
    previewUrl,
    previewMeta,
    qaSummary,
    qaBlocks,
    editBusy,
    editNote,
    elapsed,
    runner,
    pickFile,
    resetAll,
    cancelJob,
    submitJob,
    syncTranslate,
    runPreview,
    saveEdits,
  };
}
