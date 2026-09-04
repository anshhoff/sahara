import { PageSkeleton } from "@/components/Skeleton";

/** Applies to every route that does not define its own — which is all of them. */
export default function Loading() {
  return <PageSkeleton />;
}
