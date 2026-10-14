export type Mode = "job" | "sync" | "preview";

export interface LanguageOption {
  code: string;
  name: string;
  native: string;
  flag: string;
}

export const LANGUAGES: LanguageOption[] = [
  { code: "Vietnamese", name: "Tiếng Việt", native: "Vietnamese", flag: "🇻🇳" },
  { code: "English", name: "Tiếng Anh", native: "English", flag: "🇺🇸" },
  { code: "Japanese", name: "Tiếng Nhật", native: "日本語", flag: "🇯🇵" },
  { code: "Korean", name: "Tiếng Hàn", native: "한국어", flag: "🇰🇷" },
  { code: "Chinese", name: "Tiếng Trung", native: "中文", flag: "🇨🇳" },
  { code: "French", name: "Tiếng Pháp", native: "Français", flag: "🇫🇷" },
  { code: "German", name: "Tiếng Đức", native: "Deutsch", flag: "🇩🇪" },
  { code: "Spanish", name: "Tiếng Tây Ban Nha", native: "Español", flag: "🇪🇸" },
];

export interface JobState {
  job_id: string;
  status: string;
  progress: number;
  step: string;
  detail: string;
  output_filename?: string;
  error?: string;
}

export interface QaBlock {
  id: string;
  page: number;
  source?: string;
  final_text: string;
  original_masked?: string;
  translated_masked?: string;
  final_size?: number;
  issues: string[];
}

export interface QaSummary {
  total_blocks?: number;
  total_pages?: number;
  ocr_pages?: number;
  overflow_blocks?: number;
  small_font_blocks?: number;
  fallback_blocks?: number;
  quality_score?: number;
}

export const MAX_UPLOAD_BYTES = 50 * 1024 * 1024;
