import ReviewGate from '../../../review-gate';

export default async function PipelinePage(props: PageProps<'/pipelines/[id]'>) {
  const { id } = await props.params;
  return <ReviewGate context={{ pipelineId: id }} />;
}
