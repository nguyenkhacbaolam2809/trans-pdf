"use client";

import { useEffect, useState } from "react";
import { fetchHealthText } from "../lib/api";

export function useHealth(): string {
  const [text, setText] = useState("Đang kiểm tra máy chủ...");

  useEffect(() => {
    let alive = true;
    fetchHealthText()
      .then((t) => {
        if (alive) setText(t);
      })
      .catch(() => {
        if (alive)
          setText("Chưa nối được máy chủ. Kiểm tra backend ở cổng 8000.");
      });
    return () => {
      alive = false;
    };
  }, []);

  return text;
}
