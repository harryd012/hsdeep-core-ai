"use client";

import { useParams } from "next/navigation";
import NocEventDetail from "@/components/noc/NocEventDetail";

export default function NocEventDetailPage() {
  const params = useParams();
  const eventId = typeof params.id === "string" ? params.id : "";

  return (
    <div style={{ padding: "16px" }}>
      <NocEventDetail eventId={eventId} />
    </div>
  );
}
