"use client";

/**
 * Shared full-bleed brand backdrop for the auth pages (/login, /signup).
 *
 * Layer order: cleaned background video (the approved "Digital Sphere" brand
 * reference with its baked-in logo/text/login-card removed — see
 * public/branding/hsdeep-core-ai-login-bg.mp4) -> static poster (identical
 * composition) -> legibility scrim. All identity and auth UI is rendered in
 * React on top, so nothing is duplicated between the video and the page.
 *
 * Accessibility / performance:
 * - muted + playsInline so autoplay is permitted by every browser.
 * - Under `prefers-reduced-motion: reduce` the CSS hides the video and shows
 *   the static poster; this component additionally pauses the element so no
 *   decode cycles are spent on a hidden <video>.
 * - Decorative only: aria-hidden and removed from the tab order.
 */

import { useEffect, useRef } from "react";

export default function AuthBackdrop() {
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const mediaQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    const apply = () => {
      if (mediaQuery.matches && !video.paused) video.pause();
    };
    apply();
    mediaQuery.addEventListener("change", apply);
    return () => mediaQuery.removeEventListener("change", apply);
  }, []);

  return (
    <>
      <video
        ref={videoRef}
        className="login-page__video"
        autoPlay
        muted
        loop
        playsInline
        preload="metadata"
        poster="/branding/hsdeep-core-ai-login-bg.jpg"
        aria-hidden="true"
        tabIndex={-1}
      >
        <source src="/branding/hsdeep-core-ai-login-bg.mp4" type="video/mp4" />
      </video>
      {/* Static fallback: reduced motion, missing codec, or data-saver mode.
          Deliberate plain <img>: full-bleed backdrop, not optimizable content. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src="/branding/hsdeep-core-ai-login-bg.jpg"
        alt=""
        aria-hidden="true"
        className="login-page__poster"
        draggable={false}
      />
      <div className="login-page__scrim" aria-hidden="true" />
    </>
  );
}
