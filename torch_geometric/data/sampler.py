from typing import List, Optional, Tuple, NamedTuple

import torch
from torch_sparse import SparseTensor


class Adj(NamedTuple):
    edge_index: torch.Tensor
    e_id: torch.Tensor
    size: Tuple[int, int]

    def to(self, *args, **kwargs):
        return Adj(self.edge_index.to(*args, **kwargs),
                   self.e_id.to(*args, **kwargs), self.size)


class NeighborSampler(torch.utils.data.DataLoader):
    r"""The neighbor sampler from the `"Inductive Representation Learning on
    Large Graphs" <https://arxiv.org/abs/1706.02216>`_ paper, which allows
    for mini-batch training of GNNs on large-scale graphs where full-batch
    training is not feasible.

    Given a GNN with :math:`L` layers and a specific mini-batch of nodes
    :obj:`node_idx` for which we want to compute embeddings, this module
    iteratively samples neighbors and constructs bipartite graphs that simulate
    the actual computation flow of GNNs.

    More specifically, :obj:`sizes` denotes how much neighbors we want to
    sample for each node in each layer.
    This module then takes in these :obj:`sizes` and iteratively samples
    :obj:`sizes[l]` for each node involved in layer :obj:`l`.
    In the next layer, sampling is repeated for the union of nodes that were
    already encountered.
    The actual computation graphs are then returned in reverse-mode, meaning
    that we pass messages from a larger set of nodes to a smaller one, until we
    reach the nodes for which we originally wanted to compute embeddings.

    Hence, an item returned by :class:`NeighborSampler` holds the current
    :obj:`batch_size`, the IDs :obj:`n_id` of all nodes involved in the
    computation, and a list of bipartite graph objects via the tuple
    :obj:`(edge_index, e_id, size)`, where :obj:`edge_index` represents the
    bipartite edges between source and target nodes, :obj:`e_id` denotes the
    IDs of original edges in the full graph, and :obj:`size` holds the shape
    of the bipartite graph.
    For each bipartite graph, target nodes are also included at the beginning
    of the list of source nodes so that one can easily apply skip-connections
    or add self-loops.

    .. note::

        For an example of using :obj:`NeighborSampler`, see
        `examples/reddit.py
        <https://github.com/rusty1s/pytorch_geometric/blob/master/examples/
        reddit.py>`_ or
        `examples/ogbn_products_sage.py
        <https://github.com/rusty1s/pytorch_geometric/blob/master/examples/
        ogbn_products_sage.py>`_.

    Args:
        edge_index (LongTensor): The edge indices of the full-graph.
        size ([int]): The number of neighbors to
            sample for each node in each layer. If set to :obj:`sizes[i] = -1`,
            all neighbors are included in layer :obj:`l`.
        node_idx (LongTensor, optional): The nodes that should be considered
            for creating mini-batches. If set to :obj:`None`, all nodes will be
            considered.
        flow (string, optional): The flow direction of message passing
            (:obj:`"source_to_target"` or :obj:`"target_to_source"`).
            (default: :obj:`"source_to_target"`)
        **kwargs (optional): Additional arguments of
            :class:`torch.utils.data.DataLoader`, such as :obj:`batch_size`,
            :obj:`shuffle`, :obj:`drop_last` or :obj:`num_workers`.
    """
    def __init__(self, edge_index: torch.Tensor, sizes: List[int],
                 node_idx: Optional[torch.Tensor] = None,
                 num_nodes: Optional[int] = None,
                 flow: str = "source_to_target", **kwargs):

        N = int(edge_index.max() + 1) if num_nodes is None else num_nodes
        edge_attr = torch.arange(edge_index.size(1))
        adj = SparseTensor(row=edge_index[0], col=edge_index[1],
                           value=edge_attr, sparse_sizes=(N, N),
                           is_sorted=False)
        adj = adj.t() if flow == 'source_to_target' else adj
        self.adj = adj.to('cpu')

        if node_idx is None:
            node_idx = torch.arange(N)
        elif node_idx.dtype == torch.bool:
            node_idx = node_idx.nonzero().view(-1)

        self.sizes = sizes
        self.flow = flow
        assert self.flow in ['source_to_target', 'target_to_source']

        super(NeighborSampler, self).__init__(node_idx.tolist(),
                                              collate_fn=self.sample, **kwargs)

    def sample(self, batch):
        if not isinstance(batch, torch.Tensor):
            batch = torch.tensor(batch)

        batch_size: int = len(batch)
        adjs: List[Adj] = []

        n_id = batch
        for size in self.sizes:
            adj, n_id = self.adj.sample_adj(n_id, size, replace=False)
            if self.flow == 'source_to_target':
                adj = adj.t()
            row, col, e_id = adj.coo()
            size = adj.sparse_sizes()
            edge_index = torch.stack([row, col], dim=0)

            adjs.append(Adj(edge_index, e_id, size))

        if len(adjs) > 1:
            return batch_size, n_id, adjs[::-1]
        else:
            return batch_size, n_id, adjs[0]

    def __repr__(self):
        return '{}(sizes={})'.format(self.__class__.__name__, self.sizes)
