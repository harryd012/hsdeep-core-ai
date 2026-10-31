"use client";

// useSearchParams() needs a Suspense boundary under the App Router.
import { Suspense } from "react";
import { SetPasswordForm } from "@/components/auth/SetPasswordForm";

export default function Page() {
  return (
    <Suspense fallback={null}>
      <SetPasswordForm mode="reset" />
    </Suspense>
  );
}
