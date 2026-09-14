"use client";

import { useParams } from "next/navigation";
import SocAnalystDetail from "@/components/noc/SocAnalystDetail";

export default function SocAnalystDetailPage() {
  const params = useParams();
  const analystId = typeof params.id === "string" ? params.id : "";

  return (
    <div style={{ padding: "16px" }}>
      <SocAnalystDetail analystId={analystId} />
    </div>
  );
}
