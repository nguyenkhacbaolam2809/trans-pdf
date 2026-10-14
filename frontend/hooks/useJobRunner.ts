"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE, fetchJob } from "../lib/api";
import { toWsBase } from "../lib/format";
import type { JobState } from "../types";

interface UseJobRunnerOptions {
  onDone: (state: JobState) => void;
  onError: (message: string) => void;
}

/** Theo dõi 1 job qua WebSocket, tự rơi về hỏi định kỳ khi mất socket. */
export function useJobRunner({ onDone, onError }: UseJobRunnerOptions) {
  const [progress, setProgress] = useState(0);
  const [step, setStep] = useState("");
  const [detail, setDetail] = useState("");
  const wsRef = useRef<WebSocket | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const onDoneRef = useRef(onDone);
  const onErrorRef = useRef(onError);
  onDoneRef.current = onDone;
  onErrorRef.current = onError;

  const stop = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = null;
    if (wsRef.current) {
      try {
        wsRef.current.close();
      } catch {
        /* bỏ qua */
      }
      wsRef.current = null;
    }
  }, []);

  useEffect(() => stop, [stop]);

  const applyState = useCallback((j: JobState) => {
    setProgress(j.progress || 0);
    setStep(j.step || "");
    setDetail(j.detail || "");
    if (j.status === "done") {
      stop();
      onDoneRef.current(j);
    } else if (j.status === "error") {
      stop();
      onErrorRef.current(j.error || j.detail || "Job báo lỗi.");
    }
  }, [stop]);

  const watch = useCallback(
    (jobId: string) => {
      stop();
      setProgress(2);
      setStep("Đã xếp hàng");
      setDetail("");

      const startPoll = () => {
        if (pollRef.current) clearInterval(pollRef.current);
        pollRef.current = setInterval(async () => {
          try {
            applyState(await fetchJob(jobId));
          } catch {
            /* thử lại kỳ sau */
          }
        }, 2000);
      };

      try {
        const ws = new WebSocket(`${toWsBase(API_BASE)}/ws/jobs/${jobId}`);
        wsRef.current = ws;
        ws.onmessage = (ev) => {
          try {
            const data = JSON.parse(ev.data);
            // Đợt A: bỏ qua heartbeat {"type":"heartbeat"} và tin lạ không có
            // status — trước đây chúng reset tiến trình về 0%.
            if (!data || typeof data.status !== "string") return;
            applyState(data as JobState);
          } catch {
            /* bỏ qua tin rác */
          }
        };
        ws.onerror = () => startPoll();
        ws.onclose = () => {
          if (wsRef.current === ws) {
            wsRef.current = null;
            startPoll();
          }
        };
      } catch {
        startPoll();
      }
    },
    [applyState, stop]
  );

  return { progress, step, detail, watch, stop, setProgress, setStep, setDetail };
}
