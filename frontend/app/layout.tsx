import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "trans-pdf | Dịch PDF giữ bố cục",
  description:
    "Tải PDF kỹ thuật lên, dịch sang ngôn ngữ bạn chọn, giữ nguyên hình ảnh, bảng và công thức.",
};

function themeInitScript(): string {
  return `(function(){try{var t=localStorage.getItem("trans-pdf-theme");if(t==="light"||t==="dark"){if(t==="dark")document.documentElement.classList.add("dark")}else if(window.matchMedia("(prefers-color-scheme: dark)").matches){document.documentElement.classList.add("dark")}else{document.documentElement.classList.remove("dark")}}catch(e){}})();`;
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="vi" suppressHydrationWarning>
      <head>
        <script
          dangerouslySetInnerHTML={{ __html: themeInitScript() }}
        />
      </head>
      <body className="min-h-screen bg-slate-100 text-slate-900 antialiased dark:bg-slate-950 dark:text-slate-200">
        {children}
      </body>
    </html>
  );
}
