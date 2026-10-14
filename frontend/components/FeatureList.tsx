const ITEMS = [
  {
    name: "Đọc đúng chỗ chữ",
    text: "Lấy tọa độ từng đoạn chữ rồi đặt bản dịch vào đúng ô đó. Chữ dài quá thì thu nhỏ cho vừa.",
  },
  {
    name: "Giữ công thức và ảnh",
    text: "Công thức được đánh dấu trước khi dịch rồi trả về nguyên vẹn. Ảnh và hình vẽ không bị xóa.",
  },
  {
    name: "Chạy job nền",
    text: "File lớn thì gửi job và theo dõi tiến trình. File scan có OCR hỗ trợ.",
  },
];

export function FeatureList() {
  return (
    <section aria-label="Cách hoạt động" className="grid gap-3 sm:grid-cols-3">
      {ITEMS.map((f, i) => (
        <div
          key={f.name}
          className="rounded-lg border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900"
        >
          <p className="text-xs font-semibold text-teal-800 dark:text-teal-200">
            {i + 1}
          </p>
          <h2 className="mt-1 text-sm font-medium text-slate-900 dark:text-slate-100">{f.name}</h2>
          <p className="mt-1 text-xs leading-relaxed text-slate-600 dark:text-slate-400">
            {f.text}
          </p>
        </div>
      ))}
    </section>
  );
}
