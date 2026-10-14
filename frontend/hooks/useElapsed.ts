"use client";

import { useEffect, useState } from "react";

/** Đếm giây từ lúc bắt đầu. Trả về số giây và hàm bắt đầu lại. */
export function useElapsed(active: boolean): [number, () => void] {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    if (!active) return;
    const t0 = Date.now();
    setElapsed(0);
    const id = setInterval(() => {
      setElapsed(Math.floor((Date.now() - t0) / 1000));
    }, 1000);
    return () => clearInterval(id);
  }, [active]);

  return [elapsed, () => setElapsed(0)];
}
